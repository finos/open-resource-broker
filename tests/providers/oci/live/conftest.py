"""OCI live validation is opt-in; normal CI must not contact a tenancy."""


def pytest_addoption(parser):
    parser.addoption(
        "--run-oci", action="store_true", default=False, help="Provision OCI live-test workers"
    )
