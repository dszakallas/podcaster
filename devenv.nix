{
  pkgs,
  lib,
  config,
  inputs,
  dotfiles-common,
  ...
}@args:
let
  lib' = dotfiles-common.lib;
in
{
  imports = [
    dotfiles-common.devenvModules.recommended
  ];
  packages = (
    with pkgs;
    [
      git
      python312
      black
      ruff
      prek
      pyright
      uv
    ]
  );
  dotenv.enable = true;
  git-hooks = {
    package = pkgs.prek;
    hooks.ruff = {
      enable = true;
      name = "ruff";
      description = "Run Ruff on src/podcaster";
      entry = "${pkgs.uv}/bin/uv run ruff check";
      files = "^src/podcaster/.*\\.py$";
    };
    hooks.black = {
      enable = true;
      name = "black";
      description = "Run Black on src/podcaster";
      entry = "${pkgs.uv}/bin/uv run black --check";
      files = "^src/podcaster/.*\\.py$";
    };
    hooks.pyright = {
      enable = true;
      name = "pyright";
      description = "Run Pyright on src/podcaster";
      entry = "${pkgs.uv}/bin/uv run pyright";
      files = "^src/podcaster/.*\\.py$";
      pass_filenames = true;
    };
  };

  languages.python.enable = true;
  languages.python.package = pkgs.python312;
  languages.python.uv.enable = true;
  languages.python.uv.package = pkgs.uv;
  languages.python.uv.sync.enable = true;

  env.PLAYWRIGHT_BROWSERS_PATH = "${config.devenv.root}/.playwright/browsers";
  env.PLAYWRIGHT_USER_DATA_DIR = "${config.devenv.root}/.playwright/user-data";
  env.PLAYWRIGHT_OUTPUT_DIR = "${config.devenv.root}/.playwright/output";

  tasks."playwright:install" = {
    exec = "${pkgs.uv}/bin/uv run playwright install chromium";
    before = [ "devenv:enterShell" ];
    after = [ "devenv:python:uv" ];
    env.PLAYWRIGHT_BROWSERS_PATH = "${config.devenv.root}/.playwright/browsers";
    env.PLAYWRIGHT_USER_DATA_DIR = "${config.devenv.root}/.playwright/user-data";
  };

  enterShell = ''
    export PATH="$DEVENV_ROOT/.venv/bin:$PATH"
  '';

  profiles = lib'.importRec1 ./devenv args;
}
