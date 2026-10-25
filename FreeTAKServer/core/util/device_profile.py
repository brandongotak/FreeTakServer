"""device profiles: settings pushed to TAK clients as an auto-imported mission package

ATAK only shows its channels screen when its preferences enable channels for
the server, which TAK servers deliver in a device profile when a client
enrolls (/Marti/api/tls/profile/enrollment) and when it connects
(/Marti/api/device/profile/connection). The same preferences are added to the
certificate packages generated in the UI."""
import io
import uuid
import zipfile
from xml.sax.saxutils import quoteattr


def channel_pref_entries(host: str) -> str:
    """preference entries enabling channels for host, plus profile updates on
    connect so the client keeps asking for its profile"""
    return ('<entry key="prefs_enable_channels" class="class java.lang.String">true</entry>'
            f'<entry key={quoteattr("prefs_enable_channels_host-" + host)} class="class java.lang.String">true</entry>'
            '<entry key="deviceProfileEnableOnConnect" class="class java.lang.Boolean">true</entry>')


def channel_prefs_xml(host: str) -> str:
    return ("<?xml version='1.0' standalone='yes'?><preferences>"
            '<preference version="1" name="com.atakmap.app.civ_preferences">'
            f"{channel_pref_entries(host)}</preference></preferences>")


def profile_package(host: str, name: str) -> bytes:
    """a mission package the client imports on receipt, laid out like the
    official server's (one fileN/ folder per file)"""
    manifest = ('<MissionPackageManifest version="2"><Configuration>'
                f'<Parameter name="uid" value={quoteattr("ProfileMissionPackage-" + str(uuid.uuid4()))}/>'
                f'<Parameter name="name" value={quoteattr(name)}/>'
                '<Parameter name="onReceiveImport" value="true"/><Parameter name="onReceiveDelete" value="true"/>'
                '</Configuration><Contents><Content ignore="false" zipEntry="file0/enable-channels.pref"/>'
                '</Contents></MissionPackageManifest>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("MANIFEST/manifest.xml", manifest)
        package.writestr("file0/enable-channels.pref", channel_prefs_xml(host))
    return buffer.getvalue()


def request_host(host_header: str) -> str:
    """the host part of a Host header ("tak.example.com:8446" -> "tak.example.com")"""
    host = (host_header or "").strip()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host
