/**
 * Allow cleartext HTTP to the Insta360 camera (192.168.42.1) and nothing else.
 * The camera speaks plain HTTP on its own Wi-Fi AP (OSC has no TLS), so the platform defaults would block it.
 * Android: res/xml/network_security_config.xml with a single domain-config.
 * iOS: ATS exception for that exact host (+ NSAllowsLocalNetworking, which covers older iOS where IP exception
 *      entries were unreliable) and the local-network usage string.
 * Dev builds only: set FAUXEL_DEV_CLEARTEXT=1 to also allow cleartext globally so Metro on a LAN IP works.
 */
const { withAndroidManifest, withDangerousMod, withInfoPlist } = require('expo/config-plugins');
const fs = require('fs');
const path = require('path');

const CAMERA_HOST = '192.168.42.1';

function xml(devCleartext) {
  return `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="${devCleartext ? 'true' : 'false'}" />
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="false">${CAMERA_HOST}</domain>
    </domain-config>
</network-security-config>
`;
}

module.exports = function withCameraCleartext(config) {
  const dev = process.env.FAUXEL_DEV_CLEARTEXT === '1';

  config = withAndroidManifest(config, (c) => {
    const app = c.modResults.manifest.application[0];
    app.$['android:networkSecurityConfig'] = '@xml/network_security_config';
    delete app.$['android:usesCleartextTraffic']; // the security config is authoritative
    return c;
  });

  config = withDangerousMod(config, [
    'android',
    async (c) => {
      const dir = path.join(c.modRequest.platformProjectRoot, 'app/src/main/res/xml');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, 'network_security_config.xml'), xml(dev));
      return c;
    },
  ]);

  config = withInfoPlist(config, (c) => {
    const ats = c.modResults.NSAppTransportSecurity ?? {};
    ats.NSAllowsLocalNetworking = true;
    ats.NSExceptionDomains = {
      ...(ats.NSExceptionDomains ?? {}),
      [CAMERA_HOST]: { NSExceptionAllowsInsecureHTTPLoads: true, NSIncludesSubdomains: false },
    };
    c.modResults.NSAppTransportSecurity = ats;
    c.modResults.NSLocalNetworkUsageDescription =
      'Fauxel talks to your Insta360 camera over its Wi-Fi to start recordings and download files.';
    return c;
  });

  return config;
};
