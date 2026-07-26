{
  pkgs,
  lib,
  inputs,
  dotfiles-common,
  ...
}:
{
  module =
    { config, ... }:
    let
      lib' = dotfiles-common.lib;
      mcpServers = {
        playwright = {
          type = "stdio";
          command = "playwright-mcp";
          args = [ "--headless" ];
          env = {
            "PLAYWRIGHT_MCP_USER_DATA_DIR" = config.env.PLAYWRIGHT_USER_DATA_DIR;
            "PLAYWRIGHT_MCP_OUTPUT_DIR" = config.env.PLAYWRIGHT_OUTPUT_DIR;
            "PLAYWRIGHT_MCP_BROWSER" = "chromium";
            "PLAYWRIGHT_BROWSERS_PATH" = config.env.PLAYWRIGHT_BROWSERS_PATH;
          };
        };
        chrome-devtools = {
          type = "stdio";
          command = "npx";
          args = [
            "-y"
            "chrome-devtools-mcp@latest"
            "--no-usage-statistics"
            "--no-performance-crux"
          ];
          env = {
          };
        };
      };
    in
    {
      imports = [
        inputs.dotfiles-common.devenvModules.agents
      ];

      agents = {
        mcp = {
          enable = true;
          servers = mcpServers;
        };
        skills = {
          enable = true;
          entries = {
            notebooklm-py = lib'.agents.mkSkill pkgs {
              name = "notebooklm-py";
              version = "2026-07-18";
              src = pkgs.fetchFromGitHub {
                owner = "teng-lin";
                repo = "notebooklm-py";
                rev = "45fd4258e608fbb9685496f26cfcea48810c44ee";
                hash = "sha256-qMszoeocNub0xIb/09CSy1JvKQYbIxhMQhIVlXmKe9I=";
              };
            };
          };
        };
      }
      // lib.genAttrs [ "vscode" "claude" "copilot" "gemini" "opencode" ] (name: {
        enable = true;
        mcp = {
          enable = true;
          servers = lib'.agents.mcpServersForAgent name mcpServers;
        };
      });
    };
}
