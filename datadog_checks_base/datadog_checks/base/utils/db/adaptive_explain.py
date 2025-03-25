# (C) Datadog, Inc. 2025-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)

import time
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from enum import Enum
from typing import Any, Dict, Optional, TypedDict

from ddsketch import DDSketch


class VerbosityLevel(str, Enum):
    """Simple verbosity levels for query explanations."""

    DEFAULT = "default"  # Basic EXPLAIN
    HIGH_VERBOSITY = "high_verbosity"  # EXPLAIN ANALYZE or equivalent


class AdaptiveExplainConfig(TypedDict):
    """Configuration for the AdaptiveExplainManager."""

    min_duration_ms: Optional[int]  # Minimum query duration to consider
    window_size_seconds: Optional[int]  # Size of the rolling window
    max_high_verbosity_per_window: Optional[int]  # Maximum number of high verbosity explains per window


class VerbosityTracker:
    """Tracks which queries were explained with high verbosity within a time window."""

    def __init__(self, window_size_seconds: float, max_high_verbosity_per_window: int):
        self.window_size = window_size_seconds
        self.max_high_verbosity_per_window = max_high_verbosity_per_window

        # Track high verbosity queries with their timestamps in order
        self.high_verbosity_queries = OrderedDict()  # {query_key: timestamp}

    def can_add_high_verbosity(self, timestamp: float, key: str) -> bool:
        """Check if we can add another high verbosity query."""
        if len(self.high_verbosity_queries) >= self.max_high_verbosity_per_window:
            return False
        if key in self.high_verbosity_queries:
            return True
        return True

    def add_high_verbosity_query(self, timestamp: float, key: str) -> None:
        """Record a query that was explained with high verbosity."""
        if key in self.high_verbosity_queries:
            # Remove and re-add to update the order
            del self.high_verbosity_queries[key]
        self.high_verbosity_queries[key] = timestamp

    def is_already_explained(self, key: str, current_time: float) -> bool:
        """Check if a query was already explained with high verbosity within the window."""
        if key not in self.high_verbosity_queries:
            return False
        timestamp = self.high_verbosity_queries[key]
        return current_time - timestamp <= self.window_size

    def expire_old_items(self, current_time: float) -> None:
        """Remove high verbosity queries older than the window size."""
        window_start = current_time - self.window_size

        # Remove items from the beginning until we find one within the window
        while self.high_verbosity_queries:
            key, timestamp = next(iter(self.high_verbosity_queries.items()))
            if timestamp >= window_start:
                break
            self.high_verbosity_queries.popitem(last=False)


class RollingDDSketch:
    """A rolling window implementation of DDSketch that maintains buckets of sketches."""

    def __init__(self, window_seconds: int = 60, relative_accuracy: float = 0.01):
        self.window = window_seconds
        self.relative_accuracy = relative_accuracy
        self.buckets = deque()  # deque of (timestamp, DDSketch)
        self._last_merge_time = 0
        self._cached_sketch = None
        self._current_bucket_time = 0

    def add(self, value: float, timestamp: float = None) -> None:
        """Add a value to the current bucket."""
        if timestamp is None:
            timestamp = time.time()
        now = int(timestamp)

        # Create new bucket if needed
        if now != self._current_bucket_time:
            self._current_bucket_time = now
            self.buckets.append((now, DDSketch(relative_accuracy=self.relative_accuracy)))
            self._cached_sketch = None  # Invalidate cache on new bucket
        
        # Add value to current bucket
        self.buckets[-1][1].add(value)
        self._evict_old(now)

    def _evict_old(self, now: float) -> None:
        """Remove buckets older than the window."""
        while self.buckets and self.buckets[0][0] < now - self.window:
            self.buckets.popleft()
            self._cached_sketch = None  # Invalidate cache on eviction

    def _get_merged_sketch(self) -> DDSketch:
        """Get merged sketch, using cache if available."""
        now = int(time.time())
        
        # Return cached sketch if it's still valid (aligned with bucket window)
        if self._cached_sketch is not None and now == self._current_bucket_time:
            return self._cached_sketch

        # Create new merged sketch
        if not self.buckets:
            return DDSketch(relative_accuracy=self.relative_accuracy)

        combined = DDSketch(relative_accuracy=self.relative_accuracy)
        for _, sketch in self.buckets:
            combined.merge(sketch)
        
        # Cache the result
        self._cached_sketch = combined
        self._last_merge_time = now
        return combined

    def get_quantile(self, q: float) -> float:
        """Get the quantile value across all buckets."""
        return self._get_merged_sketch().get_quantile(q)


class QueryAnalyzer(ABC):
    """Base class for DBMS-specific query analyzers."""

    def __init__(self, window_size_seconds: float = 300, bucket_size_seconds: int = 60):
        self.window_size = window_size_seconds
        self.bucket_size = bucket_size_seconds
        self.metric_sketches = {}  # {metric_name: RollingDDSketch}

    def register_metric(self, name: str, relative_accuracy: float = 0.01) -> None:
        """Register a new metric to track with RollingDDSketch."""
        self.metric_sketches[name] = RollingDDSketch(
            window_seconds=self.window_size,
            relative_accuracy=relative_accuracy
        )

    def add_metric_value(self, name: str, value: float, timestamp: float = None) -> None:
        """Add a value to a metric's sketch."""
        if name in self.metric_sketches:
            self.metric_sketches[name].add(value, timestamp)

    def get_metric_p95(self, name: str) -> float:
        """Get the 95th percentile value for a metric."""
        if name in self.metric_sketches:
            return self.metric_sketches[name].get_quantile(0.95)
        return 0.0

    @abstractmethod
    def get_query_key(self, query: Dict[str, Any]) -> str:
        """Generate a unique key for the query."""
        pass

    @abstractmethod
    def should_explain_analyze(self, query: Dict[str, Any]) -> bool:
        """Determine if the query should be explained with high verbosity."""
        pass


class DefaultDurationAnalyzer(QueryAnalyzer):
    """Default implementation of QueryAnalyzer that uses duration percentiles."""

    def __init__(self, window_size_seconds: float = 300, bucket_size_seconds: int = 60, relative_accuracy: float = 0.01):
        super().__init__(window_size_seconds, bucket_size_seconds)
        self.register_metric("duration_ms", relative_accuracy)

    def get_duration_ms(self, query: Dict[str, Any]) -> float:
        """Extract query duration in milliseconds from the query dict."""
        raise NotImplementedError("Subclasses must implement this method")

    def get_query_key(self, query: Dict[str, Any]) -> str:
        """Generate a unique key for the query based on its text/hash."""
        raise NotImplementedError("Subclasses must implement this method")

    def should_explain_analyze(self, query: Dict[str, Any]) -> bool:
        """Determine if the query should be explained with high verbosity based on duration percentile."""
        duration_ms = self.get_duration_ms(query)
        if duration_ms <= 0:
            return False

        # Add the duration to our metric tracking
        self.add_metric_value("duration_ms", duration_ms)

        # Get the p95 duration
        p95_duration = self.get_metric_p95("duration_ms")
        if p95_duration <= 0:
            return False

        # If query duration exceeds p95, use high verbosity
        return duration_ms > p95_duration


class AdaptiveExplainManager:
    """Manages adaptive query explanation based on performance metrics."""

    def __init__(
        self,
        config: AdaptiveExplainConfig,
        query_analyzer: Optional[QueryAnalyzer] = None,
    ):
        # Core configuration
        self.min_duration_ms = config.get('min_duration_ms', 100)
        self.window_size_seconds = config.get('window_size_seconds', 300)
        self.max_high_verbosity_per_window = config.get('max_high_verbosity_per_window', 3)

        # Use default analyzer if none provided
        self.query_analyzer = query_analyzer or DefaultDurationAnalyzer(window_size_seconds=self.window_size_seconds)

        # Initialize verbosity tracker
        self.query_tracker = VerbosityTracker(
            self.window_size_seconds, max_high_verbosity_per_window=self.max_high_verbosity_per_window
        )

    def get_verbosity_for_query(self, query: Dict[str, Any], timestamp: float = None) -> str:
        """Determine the appropriate verbosity level for a query."""
        if timestamp is None:
            timestamp = time.time()

        # Get query duration
        duration_ms = self.query_analyzer.get_duration_ms(query)

        # Skip if below minimum duration
        if duration_ms < self.min_duration_ms:
            return VerbosityLevel.DEFAULT

        # Get query key
        query_key = self.query_analyzer.get_query_key(query)

        # Expire old items
        self.query_tracker.expire_old_items(timestamp)

        # Check if already explained with high verbosity
        if self.query_tracker.is_already_explained(query_key, timestamp):
            return VerbosityLevel.DEFAULT

        # Let the analyzer determine if we should use high verbosity
        if self.query_analyzer.should_explain_analyze(query):
            if self.query_tracker.can_add_high_verbosity(timestamp, query_key):
                self.query_tracker.add_high_verbosity_query(timestamp, query_key)
                return VerbosityLevel.HIGH_VERBOSITY

        return VerbosityLevel.DEFAULT
