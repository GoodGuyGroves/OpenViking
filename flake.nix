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
            pkgs.nginx
          ];

          shellHook = ''
            echo ""
            echo "  OpenViking multi-instance manager:"
            echo "    ov-manager start       # start all instances + proxy"
            echo "    ov-manager stop        # stop everything"
            echo "    ov-manager status      # show instance status"
            echo ""
          '';
        };
      }
    );
}
