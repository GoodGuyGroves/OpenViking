{
  description = "OpenViking context database and MCP server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = [
            pkgs.python312
            pkgs.uv
            pkgs.git
          ];

          shellHook = ''
            echo ""
            echo "  Start the OpenViking server before using the MCP or CLI:"
            echo "    mkdir -p data && openviking-server >> data/server.log 2>&1 & disown"
            echo ""
          '';
        };
      }
    );
}
