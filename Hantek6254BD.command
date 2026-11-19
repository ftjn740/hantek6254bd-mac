#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ "$1" == --stage ]]; then
    case "$2" in
        1|2|3) edition="releases/stage-${2}.pyz" ;;
        *) print -u2 'Choose --stage 1, 2 or 3'; exit 2 ;;
    esac
    shift 2
    exec .scope-env/bin/python -u "$edition" "$@"
fi
exec .scope-env/bin/python -u scope.py "$@"
