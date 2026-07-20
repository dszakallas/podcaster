{
  ai-sdk-anthropic,
  bash,
  buildEnv,
  cacert,
  coreutils,
  dockerTools,
  ffmpeg,
  opencode,
  playwright-driver,
  playwright-mcp,
  podcaster,
  rclone,
  replaceVars,
  rsync,
  runCommand,
}:
let
  playwright-browsers = playwright-driver.selectBrowsers {
    withWebkit = false;
    withChromium = false;
    withChromiumHeadlessShell = true;
    withFirefox = false;
    withFfmpeg = true;
  };

  opencodeConfig = replaceVars ./opencode.json {
    playwrightMcpPath = "${playwright-mcp}/bin/playwright-mcp";
    playwrightBrowsersPath = "${playwright-browsers}";
    anthropicSdkPath = "${ai-sdk-anthropic}/lib/node_modules/@ai-sdk/anthropic";
  };

  opencodeConfigDir = runCommand "opencode-config-dir" { } ''
    mkdir -p $out/etc
    cp ${opencodeConfig} $out/etc/opencode.json
  '';
in
dockerTools.buildImage {
  name = "podcaster";
  tag = "latest";

  copyToRoot = buildEnv {
    name = "podcaster-image-root";
    paths = [
      podcaster
      cacert
      bash
      coreutils
      ffmpeg
      rsync
      rclone
      opencode
      playwright-mcp
      opencodeConfigDir
    ];
    pathsToLink = [
      "/bin"
      "/etc"
    ];
  };

  config = {
    Entrypoint = [ "/bin/podcaster" ];
    Env = [
      "HOME=/workspace"
      "PLAYWRIGHT_BROWSERS_PATH=${playwright-browsers}"
      "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true"
      "OPENCODE_CONFIG=/etc/opencode.json"
    ];
  };
}
