# WH panels (strony lounge pod White-Hats Client)

Ten node serwuje zwykły Micron. White-Hats Client umie dodatkowo czytać markery `#wh:`. NomadNet ich nie pokazuje.

Pełny opis: w repozytorium klienta `reticulum-client/docs/wh-panels.md`.

Skrót:

```
#!wh=1

#wh:panel id=chat side=left size=0.7 src=/page/chat.mu refresh_time=2s
#wh:end

#wh:panel id=rooms side=right size=0.3
> Kanaly
#wh:end
```

Klucze zawsze z nazwą: `id=` `side=` `size=` oraz opcjonalnie `src=` `refresh_time=`.  
`side`: `left` `right` `top` `bottom`.  
`size`: `0.0`–`1.0`.  
`refresh_time`: `500ms` `2s` `1m`.
