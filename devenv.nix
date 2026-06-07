{ pkgs, lib, config, inputs, ... }@args:

{
  packages = [ pkgs.git ];
  dotenv.enable = true;

  languages.python.enable = true;
  languages.python.package = pkgs.python312;
  languages.python.uv.enable = true;
  languages.python.uv.sync.enable = true;

  env.PLAYWRIGHT_BROWSERS_PATH = "${config.devenv.root}/.playwright/browsers";

  tasks."playwright:install" = {
    exec = "uv run playwright install chromium";
    before = [ "devenv:enterShell" ];
    after = [ "devenv:python:uv" ];
    env.PLAYWRIGHT_BROWSERS_PATH = "${config.devenv.root}/.playwright/browsers";
  };

  profiles = {
    agents.module = import ./devenv/profiles/agents args;
  };
}
