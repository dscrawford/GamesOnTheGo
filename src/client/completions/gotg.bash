# bash completion for gotg.
#
# The lists come from `gotg complete`, which reads the cached catalog and never
# fetches — a tab that blocks on the network is worse than no completion. Ids
# are safe to word-split on because the entry-id contract has no spaces in them.

# Ids that match anywhere, not only at the front.
#
# There are thousands of them and they begin with a region — usa., world.,
# jpn., eur. — which nobody remembers and which gives the whole set no common
# prefix for bash to insert. So a plain prefix match on `metroid` offers
# nothing, while an empty prefix offers all of them and readline asks whether
# you would like to see two thousand possibilities.
#
# Matching on any part turns that into the search it wants to be: `metroid`
# finds world.super_metroid. -F because an id is a literal here, not a pattern,
# and a stray `.` or `+` in what has been typed should match itself.
_gotg_ids() {
    local cur="$1"
    if [[ -z "$cur" ]]; then
        gotg complete ids
    else
        gotg complete ids | grep -iF -- "$cur"
    fi
}

_gotg() {
    local cur prev cmd sub
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD - 1]}"
    cmd="${COMP_WORDS[1]:-}"
    sub="${COMP_WORDS[2]:-}"
    COMPREPLY=()

    local commands="login refresh list info download install uninstall play configure steam saves controllers admin sync help"

    if [[ $COMP_CWORD -eq 1 ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$commands" -- "$cur")
        return
    fi

    # --limit wants a number, and nothing sensible can be offered for one.
    if [[ "$prev" == "--limit" ]]; then
        return
    fi

    case "$cmd" in
        info | download | install | uninstall)
            [[ $COMP_CWORD -eq 2 ]] &&
                mapfile -t COMPREPLY < <(_gotg_ids "$cur")
            ;;
        play | configure)
            if [[ $COMP_CWORD -eq 2 ]]; then
                mapfile -t COMPREPLY < <(_gotg_ids "$cur")
            elif [[ $COMP_CWORD -eq 3 ]]; then
                mapfile -t COMPREPLY < <(compgen -W "$(gotg complete variants "$sub")" -- "$cur")
            fi
            ;;
        list)
            if [[ "$prev" == "--platform" ]]; then
                mapfile -t COMPREPLY < <(compgen -W "$(gotg complete platforms)" -- "$cur")
            elif [[ "$cur" == -* ]]; then
                mapfile -t COMPREPLY < <(compgen -W "--all --limit --platform --search --page" -- "$cur")
            else
                # A pattern is a regex over ids, titles and platforms. Offering
                # the ids makes the common case — completing one — work, and
                # anything else is still typed by hand.
                mapfile -t COMPREPLY < <(
                    _gotg_ids "$cur"
                    compgen -W "$(gotg complete platforms)" -- "$cur"
                )
            fi
            ;;
        saves)
            case $COMP_CWORD in
                2) mapfile -t COMPREPLY < <(compgen -W "setup status push pull adopt" -- "$cur") ;;
                *)
                    # setup takes the remote's URL, which nothing here knows and
                    # a list of game ids is actively unhelpful for.
                    [[ "$sub" == "setup" ]] && return
                    if [[ "$cur" == -* ]]; then
                        mapfile -t COMPREPLY < <(compgen -W "--all --yes --force" -- "$cur")
                    else
                        mapfile -t COMPREPLY < <(_gotg_ids "$cur")
                    fi
                    ;;
            esac
            ;;
        steam)
            # --from takes a path, so hand it to the shell's own file
            # completion; --as takes one of the five names a picture can be.
            case "$prev" in
                --from)
                    mapfile -t COMPREPLY < <(compgen -f -- "$cur")
                    return
                    ;;
                --as)
                    mapfile -t COMPREPLY < <(compgen -W "tile capsule hero logo icon" -- "$cur")
                    return
                    ;;
            esac

            case $COMP_CWORD in
                2) mapfile -t COMPREPLY < <(compgen -W "add remove art list" -- "$cur") ;;
                3)
                    case "$sub" in
                        add | remove | art) mapfile -t COMPREPLY < <(_gotg_ids "$cur") ;;
                    esac
                    ;;
                *)
                    if [[ "$cur" == -* ]]; then
                        # Only `art` has options; the others take a variant and
                        # nothing else, so offering flags there would be a lie.
                        [[ "$sub" == "art" ]] &&
                            mapfile -t COMPREPLY < <(compgen -W "--force --from --as" -- "$cur")
                    elif [[ $COMP_CWORD -eq 4 ]]; then
                        case "$sub" in
                            add | remove | art) mapfile -t COMPREPLY < <(compgen -W "$(gotg complete variants "${COMP_WORDS[3]}")" -- "$cur") ;;
                        esac
                    fi
                    ;;
            esac
            ;;
        admin)
            case $COMP_CWORD in
                2) mapfile -t COMPREPLY < <(compgen -W "invite tokens revoke import scan" -- "$cur") ;;
                *)
                    case "$sub" in
                        invite) mapfile -t COMPREPLY < <(compgen -W "--ttl --user" -- "$cur") ;;
                        scan) mapfile -t COMPREPLY < <(compgen -W "--since --all --json" -- "$cur") ;;
                        import) mapfile -t COMPREPLY < <(compgen -W "--follow --timeout" -- "$cur") ;;
                    esac
                    ;;
            esac
            ;;
        controllers)
            case $COMP_CWORD in
                2) mapfile -t COMPREPLY < <(compgen -W "list order apply" -- "$cur") ;;
                *)
                    case "$sub" in
                        order) mapfile -t COMPREPLY < <(compgen -W "--set --clear --json" -- "$cur") ;;
                        apply)
                            mapfile -t COMPREPLY < <(
                                _gotg_ids "$cur"
                                compgen -W "--all" -- "$cur"
                            )
                            ;;
                    esac
                    ;;
            esac
            ;;
    esac
}

complete -F _gotg gotg
