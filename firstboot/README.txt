Po nagraniu Raspberry Pi OS Lite 64-bit Imagerem (Wi-Fi + SSH):

1. Otwórz partycję boot karty na komputerze (BOOT / bootfs).
2. Skopiuj tu rns-firstboot.sh
3. W firstrun.sh (jeśli Imager go zrobił) dopisz PRZED linią która usuwa firstrun:

   bash /boot/firmware/rns-firstboot.sh || bash /boot/rns-firstboot.sh || true

4. W rns-firstboot.sh zmień YOURUSER na swoje publiczne repo.

5. Wysuń kartę, włóż do Pi Zero 2W, zasilanie.

Pierwszy start może zająć kilka minut (apt + pip).
Potem każdy boot: poczekaj ~2 min i wejdź na http://IP:4240
