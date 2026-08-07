# bash completion for gotg.
#
# The lists come from `gotg complete`, which reads the cached catalog and never
# fetches — a tab that blocks on the network is worse than no completion. Ids
# are safe to word-split on because the entry-id contract has no spaces in them.

_gotg() {
    local cur prev cmd sub
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD - 1]}"
    cmd="${COMP_WORDS[1]:-}"
    sub="${COMP_WORDS[2]:-}"
    COMPREPLY=()

    local commands="login refresh list info download install play configure saves controllers sync help"

    if [[ $COMP_CWORD -eq 1 ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$commands" -- "$cur")
        return
    fi

    # --limit wants a number, and nothing sensible can be offered for one.
    if [[ "$prev" == "--limit" ]]; then
        return
    fi

    case "$cmd" in
        info | download | install)
            [[ $COMP_CWORD -eq 2 ]] &&
                mapfile -t COMPREPLY < <(compgen -W "$(gotg complete ids)" -- "$cur")
            ;;
        play | configure)
            if [[ $COMP_CWORD -eq 2 ]]; then
                mapfile -t COMPREPLY < <(compgen -W "$(gotg complete ids)" -- "$cur")
            elif [[ $COMP_CWORD -eq 3 ]]; then
                mapfile -t COMPREPLY < <(compgen -W "$(gotg complete variants "$sub")" -- "$cur")
            fi
            ;;
        list)
            if [[ "$cur" == -* ]]; then
                mapfile -t COMPREPLY < <(compgen -W "--all --limit" -- "$cur")
            else
                # A pattern is a regex over ids, titles and platforms. Offering
                # the ids makes the common case — completing one — work, and
                # anything else is still typed by hand.
                mapfile -t COMPREPLY < <(compgen -W "$(gotg complete ids) $(gotg complete platforms)" -- "$cur")
            fi
            ;;
        saves)
            case $COMP_CWORD in
                2) mapfile -t COMPREPLY < <(compgen -W "setup status push pull adopt" -- "$cur") ;;
                *)
                    if [[ "$cur" == -* ]]; then
                        mapfile -t COMPREPLY < <(compgen -W "--all --yes --force" -- "$cur")
                    else
                        mapfile -t COMPREPLY < <(compgen -W "$(gotg complete ids)" -- "$cur")
                    fi
                    ;;
            esac
            ;;
        controllers)
            case $COMP_CWORD in
                2) mapfile -t COMPREPLY < <(compgen -W "list order apply" -- "$cur") ;;
                *)
                    case "$sub" in
                        order) mapfile -t COMPREPLY < <(compgen -W "--set --clear --json" -- "$cur") ;;
                        apply) mapfile -t COMPREPLY < <(compgen -W "--all $(gotg complete ids)" -- "$cur") ;;
                    esac
                    ;;
            esac
            ;;
    esac
}

complete -F _gotg gotg
