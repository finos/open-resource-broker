"""Simple help utilities for CLI commands."""


def print_getting_started_help():
    """Print getting started help with most common commands."""
    from cli.console import print_command, print_info
    
    print_info("To get started:")
    print_command("  orb init --interactive       # Set up infrastructure discovery")
    print_command("  orb templates generate       # Generate example templates")
    print_info("")
    
    print_info("Infrastructure management:")
    print_command("  orb infrastructure discover  # Discover cloud infrastructure")
    print_command("  orb infrastructure show      # Show current infrastructure")
    print_info("")
    
    print_info("Example workflow:")
    print_command("  $ orb init --interactive")
    print_command("  $ orb templates generate") 
    print_command("  $ orb templates list")
    print_command("  $ orb machines request my-template 3")
