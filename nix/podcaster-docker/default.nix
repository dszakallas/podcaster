{
  name ? "podcaster",
  ai-sdk-anthropic,
  antigravity-cli,
  bash,
  buildEnv,
  cacert,
  coreutils,
  dockerTools,
  ffmpeg,
  lib,
  opencode,
  playwright-driver,
  playwright-mcp,
  podcaster,
  rclone,
  replaceVars,
  rsync,
  runCommand,
  withAgents ? [
    "opencode"
    "antigravity-cli"
  ],
}:
let
  hasAgents = withAgents != null && (lib.length withAgents > 0);
  hasOpencode = withAgents != null && (lib.elem "opencode" withAgents);
  hasAntigravity = withAgents != null && (lib.elem "antigravity-cli" withAgents);

  playwright-browsers = playwright-driver.selectBrowsers {
    withWebkit = !hasAgents;
    withChromium = false;
    withChromiumHeadlessShell = hasAgents;
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

  antigravityConfig = replaceVars ./antigravity.json {
    playwrightMcpPath = "${playwright-mcp}/bin/playwright-mcp";
    playwrightBrowsersPath = "${playwright-browsers}";
  };

  antigravityConfigDir = runCommand "antigravity-config-dir" { } ''
    mkdir -p $out/workspace/.gemini/config
    cp ${antigravityConfig} $out/workspace/.gemini/config/mcp_config.json
  '';

  agentPaths =
    lib.optionals hasAgents [ playwright-mcp ]
    ++ lib.optionals hasOpencode [
      opencode
      opencodeConfigDir
    ]
    ++ lib.optionals hasAntigravity [
      antigravity-cli
      antigravityConfigDir
    ];

  agentEnvs = lib.optionals hasOpencode [ "OPENCODE_CONFIG=/etc/opencode.json" ];
in
dockerTools.buildImage {
  inherit name;
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
    ]
    ++ agentPaths;
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
    ]
    ++ agentEnvs;
  };
}
