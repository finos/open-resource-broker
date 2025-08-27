import os
import structlog
from dynaconf import Dynaconf

# Load configuration
settings = Dynaconf(
    settings_files=["awsprov_config.json"],
    environments=True,
    env_switcher="HF_PROVIDER_ENV",
    load_dotenv=True,
)

def setup_logging(
    log_dir: str = None,
    log_filename: str = None,
    log_level: str = None,
    log_destination: str = None
):
    """
    Set up structured logging for the application using structlog.

    :param log_dir: Directory where the log file will be stored.
    :param log_filename: Name of the log file.
    :param log_level: Logging level (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL).
    :param log_destination: Where to send logs ("file", "stdout", or "both").
    :return: Configured structlog logger instance.
    """
    # Use configuration values, with fallbacks to environment variables and defaults
    provider_name = settings.get('HF_PROVIDER_NAME', os.environ.get("HF_PROVIDER_NAME", "default"))
    log_dir = log_dir or settings.get('LOG_DIR', os.environ.get("HF_PROVIDER_LOGDIR", f"./{provider_name}/logs"))
    log_filename = log_filename or settings.get('LOG_FILENAME', f"{provider_name}_log.log")
    log_level = log_level or settings.get('LOG_LEVEL', "INFO")
    log_destination = log_destination or settings.get('LOG_DESTINATION', "both")

    # Ensure the log directory exists
    os.makedirs(log_dir, exist_ok=True)

    # Configure logging handlers
    handlers = []
    if log_destination in ("file", "both"):
        file_handler = structlog.stdlib.StreamHandler(open(os.path.join(log_dir, log_filename), "a"))
        handlers.append(file_handler)

    if log_destination in ("stdout", "both"):
        stream_handler = structlog.stdlib.StreamHandler()
        handlers.append(stream_handler)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper(), logging.INFO),
        handlers=handlers,
    )

    return structlog.get_logger(provider_name)

# Initialize a global logger instance
logger = setup_logging()
