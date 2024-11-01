# (C) Datadog, Inc. 2024-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)

from pymongo.errors import OperationFailure

from datadog_checks.mongo.collectors.base import MongoCollector


class HostInfoCollector(MongoCollector):
    """
    Collects underlying system metrics that the mongod or mongos runs on using the 'hostInfo' mongo command.
    """

    def compatible_with(self, deployment):
        # Can theoretically be run on any mongod or mongos node.
        return True

    def collect(self, api):
        try:
            host_info = api['admin'].command('hostInfo')
            return self._submit_payload({'system': host_info.get('system', {})})
        except OperationFailure as e:
            if e.code == 8000:
                # The hostInfo command is not supported on MongoDB Atlas shared or serverless clusters.
                self.log.debug('The hostInfo command is not supported on this version of MongoDB')
                return
            self.log.warning('Failed to collect host info: %s', e)
