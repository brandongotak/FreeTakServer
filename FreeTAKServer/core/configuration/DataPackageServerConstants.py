import json
import os
import pathlib
from FreeTAKServer.core.configuration.MainConfig import MainConfig

# Make a connection to the MainConfig object for all routines below
config = MainConfig.instance()

class DataPackageServerConstants:
    def __init__(self):
        # http server config
        self.DATABASE = 'FreeTAKServerDataPackageDataBase.db'
        self.APIPORT = '8080'
        self.DEFAULTRETURN = 'other'
        self.GET = 'GET'
        self.PUT = 'PUT'
        self.POST = 'POST'
        self.DATAPACKAGEFOLDER = 'FreeTAKServerDataPackageFolder'
        self.HTTPDEBUG = False
        self.HTTPMETHODS = ['POST', 'GET', 'PUT']
        self.IP = "0.0.0.0"
        self.versionInfo = config.version
        self.NodeID = 'FTS'
        # shown by clients as the server version; derived from the real FTS
        # version ("FreeTAKServer-2.2.1" -> "2.2.1-FTS-RELEASE") instead of the
        # old hardcoded "2.1 RC1"
        server_version = str(config.version).replace("FreeTAKServer-", "")
        self.VERSIONJSON = json.dumps({
            "version": "3",
            "type": "ServerConfig",
            "data": {"version": f"{server_version}-FTS-RELEASE", "api": config.APIVersion, "hostname": "0.0.0.0"},
            "nodeId": config.nodeID,
        })
