{
  pkgs,
  lib,
  inputs,
  dotfiles-common,
  ...
}:

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
      args = [ "-y" "chrome-devtools-mcp@latest" "--no-usage-statistics" "--no-performance-crux" ];
      env = {
      };
    };
  };
in
{
  imports = [
    inputs.dotfiles-common.devenvModules.agents
  ];

  env.PLAYWRIGHT_BROWSERS_PATH = "${config.devenv.root}/.playwright/browsers";
  env.PLAYWRIGHT_USER_DATA_DIR = "${config.devenv.root}/.playwright/user-data";
  env.PLAYWRIGHT_OUTPUT_DIR = "${config.devenv.root}/.playwright/output";

  tasks."playwright:install" = {
    exec = "${pkgs.uv}/bin/uv run playwright install chromium";
    before = [ "devenv:enterShell" ];
    after = [ "devenv:python:uv" ];
    env.PLAYWRIGHT_BROWSERS_PATH = "${config.devenv.root}/.playwright/browsers";
  };

  agents = {
    mcp = {
      enable = true;
      servers = mcpServers;
    };
  } // lib.genAttrs [ "vscode" "claude" "copilot" "gemini" "opencode" ] (name: {
    enable = true;
    mcp = {
      enable = true;
      servers = lib'.agents.mcpServersForAgent name mcpServers;
    };
  });
}
