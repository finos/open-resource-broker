{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };
  outputs =
    {
      self,
      nixpkgs,
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      flakePkgs = pkgs.callPackages ./. { };
      defaultPackage = flakePkgs.open-resource-broker;
    in
    {
      defaultPackage.${system} = defaultPackage;
      packages.x86_64-linux = flakePkgs;
    };
}
