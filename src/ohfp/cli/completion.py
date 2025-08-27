"""
Shell completion scripts for the CLI.

This module provides bash and zsh completion scripts for the ohfp command,
enabling tab completion for commands, options, and arguments.
"""


def generate_bash_completion() -> str:
    """Generate bash completion script for ohfp command."""
    return """#!/bin/bash

_ohfp_completion() {
    local cur prev words cword
    _init_completion || return

    local resources="templates machines requests providers storage system config"
    local global_opts="--config --log-level --format --output --quiet --verbose --dry-run --completion --version --help"

    # Handle global options with values
    case $prev in
        --log-level)
            COMPREPLY=($(compgen -W "DEBUG INFO WARNING ERROR CRITICAL" -- "$cur"))
            return 0
            ;;
        --format)
            COMPREPLY=($(compgen -W "json yaml table list" -- "$cur"))
            return 0
            ;;
        --config)
            COMPREPLY=($(compgen -f -- "$cur"))
            return 0
            ;;
        --output)
            COMPREPLY=($(compgen -f -- "$cur"))
            return 0
            ;;
    esac

    # Complete resource names
    if [[ $cword -eq 1 ]]; then
        COMPREPLY=($(compgen -W "$resources" -- "$cur"))
        return 0
    fi

    # Complete actions based on resource
    if [[ $cword -eq 2 ]]; then
        case ${words[1]} in
            templates)
                COMPREPLY=($(compgen -W "list show create update delete validate" -- "$cur"))
                ;;
            machines)
                COMPREPLY=($(compgen -W "list show create terminate status" -- "$cur"))
                ;;
            requests)
                COMPREPLY=($(compgen -W "list show create cancel status" -- "$cur"))
                ;;
            providers)
                COMPREPLY=($(compgen -W "list show configure test" -- "$cur"))
                ;;
            storage)
                COMPREPLY=($(compgen -W "list show create delete mount unmount" -- "$cur"))
                ;;
            system)
                COMPREPLY=($(compgen -W "status health metrics logs" -- "$cur"))
                ;;
            config)
                COMPREPLY=($(compgen -W "show set get validate reset" -- "$cur"))
                ;;
        esac
        return 0
    fi

    # Complete options for specific commands
    if [[ $cword -ge 3 ]]; then
        case "${words[1]} ${words[2]}" in
            "templates list")
                COMPREPLY=($(compgen -W "--provider-api --long --format" -- "$cur"))
                ;;
            "templates show")
                COMPREPLY=($(compgen -W "--format --legacy" -- "$cur"))
                ;;
            "machines list")
                COMPREPLY=($(compgen -W "--status --template-id --format" -- "$cur"))
                ;;
            "requests list")
                COMPREPLY=($(compgen -W "--status --template-id --format" -- "$cur"))
                ;;
        esac
    fi

    # Default to global options
    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
}

complete -F _ohfp_completion ohfp
complete -F _ohfp_completion open-hostfactory-plugin
"""


def generate_zsh_completion() -> str:
    """Generate zsh completion script for ohfp command."""
    return """#compdef ohfp open-hostfactory-plugin

_ohfp() {
    local context state line
    typeset -A opt_args

    _arguments -C \
        '1: :_ohfp_resources' \
        '2: :_ohfp_actions' \
        '*: :_ohfp_options' \
        '--config[Configuration file]:file:_files' \
        '--log-level[Log level]:(DEBUG INFO WARNING ERROR CRITICAL)' \
        '--format[Output format]:(json yaml table list)' \
        '--output[Output file]:file:_files' \
        '--quiet[Quiet mode]' \
        '--verbose[Verbose mode]' \
        '--dry-run[Dry run mode]' \
        '--completion[Generate completion script]:(bash zsh)' \
        '--version[Show version]' \
        '--help[Show help]'
}

_ohfp_resources() {
    local resources
    resources=(
        'templates:Manage compute templates'
        'machines:Manage compute instances'
        'requests:Manage provisioning requests'
        'providers:Manage cloud providers'
        'storage:Manage storage resources'
        'system:System operations'
        'config:Configuration management'
    )
    _describe 'resources' resources
}

_ohfp_actions() {
    case $words[2] in
        templates)
            local actions=(
                'list:List all templates'
                'show:Show template details'
                'create:Create new template'
                'update:Update existing template'
                'delete:Delete template'
                'validate:Validate template'
            )
            _describe 'template actions' actions
            ;;
        machines)
            local actions=(
                'list:List all machines'
                'show:Show machine details'
                'create:Create new machine'
                'terminate:Terminate machine'
                'status:Check machine status'
            )
            _describe 'machine actions' actions
            ;;
        requests)
            local actions=(
                'list:List all requests'
                'show:Show request details'
                'create:Create new request'
                'cancel:Cancel request'
                'status:Check request status'
            )
            _describe 'request actions' actions
            ;;
        providers)
            local actions=(
                'list:List providers'
                'show:Show provider details'
                'configure:Configure provider'
                'test:Test provider connection'
            )
            _describe 'provider actions' actions
            ;;
        storage)
            local actions=(
                'list:List storage resources'
                'show:Show storage details'
                'create:Create storage'
                'delete:Delete storage'
                'mount:Mount storage'
                'unmount:Unmount storage'
            )
            _describe 'storage actions' actions
            ;;
        system)
            local actions=(
                'status:System status'
                'health:Health check'
                'metrics:System metrics'
                'logs:View logs'
            )
            _describe 'system actions' actions
            ;;
        config)
            local actions=(
                'show:Show configuration'
                'set:Set configuration value'
                'get:Get configuration value'
                'validate:Validate configuration'
                'reset:Reset configuration'
            )
            _describe 'config actions' actions
            ;;
    esac
}

_ohfp_options() {
    case "$words[2] $words[3]" in
        "templates list")
            _arguments \
                '--provider-api[Filter by provider API]:provider:' \
                '--long[Include detailed fields]' \
                '--format[Output format]:(json yaml table list)'
            ;;
        "templates show")
            _arguments \
                '--format[Output format]:(json yaml table list)' \
            ;;
        "machines list")
            _arguments \
                '--status[Filter by status]:status:' \
                '--template-id[Filter by template]:template:' \
                '--format[Output format]:(json yaml table list)'
            ;;
        "requests list")
            _arguments \
                '--status[Filter by status]:status:' \
                '--template-id[Filter by template]:template:' \
                '--format[Output format]:(json yaml table list)'
            ;;
    esac
}

_ohfp "$@"
"""
