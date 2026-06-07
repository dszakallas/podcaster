{
  pkgs,
  lib,
  config,
  inputs,
  dotfiles-common,
  ...
}:
let
  lib' = dotfiles-common.lib;
  mcpServers = {
    playwright = {
      type = "stdio";
      command = "playwright-mcp";
      env = {
        "PLAYWRIGHT_MCP_USER_DATA_DIR" = "${config.devenv.root}/.playwright/user-data";
        "PLAYWRIGHT_MCP_OUTPUT_DIR" = "${config.devenv.root}/.playwright/output";
        "PLAYWRIGHT_MCP_BROWSER" = "chromium";
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
    gemini = {
      enable = true;
      settings = {
        enable = true;
        value = (lib'.agents.mcpServersForAgent "gemini" mcpServers) // {
          context = {
            fileName = [ "AGENTS.md" ];
          };
        };
      };
    };
  } // lib.genAttrs [ "vscode" "claude" "copilot" ] (name: {
    enable = true;
    mcp = {
      enable = true;
      servers = lib'.agents.mcpServersForAgent name mcpServers;
    };
  });
}
