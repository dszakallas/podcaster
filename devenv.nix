{ pkgs, lib, config, inputs, ... }@args:

{
  packages = (with pkgs; [
    git
    python312
    black
    ruff
    prek
    pyright
    uv
  ]);
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

  profiles = {
    agents.module = import ./devenv/profiles/agents args;
  };
}
