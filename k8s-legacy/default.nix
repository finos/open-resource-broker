{
  callPackages,
  python312,
  nix-gitignore,
}:
let
  py = python312;
  makevenv = ./makevenv.sh;

  open-resource-broker = py.pkgs.buildPythonPackage rec {
    pname = "open-resource-broker";
    version = "1.0";
    pyproject = true;

    shellHook = ''
    source ${makevenv}
    '';

    # TODO: move source up one level, otherwise app is rebuilt every time
    #       there is a change in root level expressions.
    src =
      nix-gitignore.gitignoreSource
        [
          "helm"
          "deployments"
          "bin"
          "docs"
          "tools"
          ./.gitignore
        ]
        ./.;

    nativeBuildInputs = with py.pkgs; [
      setuptools
      wheel
      pep517
      pip
      hatchling
      pytest-cov
      mypy
      httpx
    ];

    nativeCheckInputs = with py.pkgs; [
      pytestCheckHook
      pytest-mock
      ruff
      mypy
    ];

    propagatedBuildInputs = with py.pkgs; [
      click
      kubernetes
      boto3
      jinja2
      typing-extensions
      inotify
      wrapt
      rich
      pydantic
      pydantic-settings
      tenacity
      prometheus-client
      sqlalchemy
      psycopg2
      alembic
      fastapi
      uvicorn
    ];

    # Skip install check phase - tests are run in CI via python-app.yml workflow
    doInstallCheck = false;
  };
in
{ inherit open-resource-broker; }
