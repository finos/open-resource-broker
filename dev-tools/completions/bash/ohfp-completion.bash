#!/bin/bash
# shellcheck disable=SC2207  # Bash completion uses array assignment patterns

_ohfp_completion() {
    local cur prev words
    _init_completion || return

    local resources="templates machines requests providers storage system config"
    local global_opts="--config --log-level --format --output --quiet --verbose --dry-run --completion --version --help"
    
    # Handle global options with values
    case $prev in
        --log-level)
            COMPREPLY=($(compgen -W "DEBUG INFO WARNING ERROR CRITICAL" -- "$cur"))
            return
            ;;
        --format)
            COMPREPLY=($(compgen -W "json yaml table" -- "$cur"))
            return
            ;;
        --completion)
            COMPREPLY=($(compgen -W "bash zsh" -- "$cur"))
            return
            ;;
        --config|--output)
            _filedir
            return
            ;;
    esac
    
    # Resource-specific completion logic
    case ${words[1]} in
        templates)
            case ${words[2]} in
                list)
                    COMPREPLY=($(compgen -W "--provider-api $global_opts" -- "$cur"))
                    ;;
                show|validate)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                reload)
                    COMPREPLY=($(compgen -W "--config-path $global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "list show validate reload $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        machines)
            case ${words[2]} in
                request)
                    COMPREPLY=($(compgen -W "--data --wait $global_opts" -- "$cur"))
                    ;;
                return|terminate)
                    COMPREPLY=($(compgen -W "--force --wait $global_opts" -- "$cur"))
                    ;;
                list)
                    COMPREPLY=($(compgen -W "--status --template $global_opts" -- "$cur"))
                    ;;
                show)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "request return list show terminate $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        requests)
            case ${words[2]} in
                status|show|cancel)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                list)
                    COMPREPLY=($(compgen -W "--status --limit $global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "status list show cancel $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        providers)
            case ${words[2]} in
                health|list|metrics)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                select)
                    COMPREPLY=($(compgen -W "--provider $global_opts" -- "$cur"))
                    ;;
                exec)
                    COMPREPLY=($(compgen -W "--command $global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "health list metrics select exec $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        storage)
            case ${words[2]} in
                migrate)
                    COMPREPLY=($(compgen -W "--source --target $global_opts" -- "$cur"))
                    ;;
                list|show|test)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "list show migrate test $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        system)
            case ${words[2]} in
                status|init-db)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "status init-db $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        config)
            case ${words[2]} in
                show|validate|reload)
                    COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
                    ;;
                *)
                    COMPREPLY=($(compgen -W "show validate reload $global_opts" -- "$cur"))
                    ;;
            esac
            ;;
        *)
            COMPREPLY=($(compgen -W "$resources $global_opts" -- "$cur"))
            ;;
    esac
}

complete -F _ohfp_completion ohfp open-hostfactory-plugin

