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
            echo "  OpenViking (docker-compose):"
            echo "    docker compose up -d       # start all services"
            echo "    docker compose ps          # show service status"
            echo "    docker compose down        # stop everything"
            echo "    docker compose logs -f     # follow logs"
            echo ""
          '';
        };
      }
    );
}
