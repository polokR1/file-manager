# PoloQP File Organizer

PoloQP File Organizer 1.3.1 pomaga porządkować pobrane pliki, zdjęcia i dokumenty na Windows 10 i 11 x64. Aplikacja działa lokalnie i ma interfejs po polsku i angielsku.

- Podgląd pokazuje, do jakich folderów trafią pliki. Możesz wybrać pliki i zdecydować, czy je skopiować, czy przenieść.
- Daty z metadanych obsługiwanych zdjęć i dokumentów Office można wykorzystać do grupowania. Aplikacja oferuje też osobne tryby według daty utworzenia i modyfikacji pliku.
- Raport analizy pokazuje m.in. liczbę i rozmiar plików, a skan duplikatów porównuje zawartość. Wybrane kopie można przenieść do Kosza Windows.
- Profile zapisują reguły sortowania, a lokalna historia pozwala cofnąć obsługiwane przeniesienia.

## Uruchomienie i testy

Użyj 64-bitowego Pythona z Tkinterem na Windows. W środowisku używanym do budowy zainstaluj zależności i uruchom aplikację z katalogu repozytorium:

```powershell
python -m pip install -r requirements-build.txt
python organizer.py
python -m unittest discover -s tests
```

## Budowanie instalatora

Zainstaluj NSIS 3 i udostępnij `makensis.exe` w zmiennej PATH. W PowerShell uruchom:

```powershell
.\scripts\build-windows.ps1
```

Skrypt zapisze instalator w `dist/FileOrganizer-Setup-x64-1.3.1.exe`. Instalator tworzony przez ten skrypt nie ma podpisu cyfrowego.

## Wydanie 1.3.1

Naprawiono otwieranie Ustawień i zachowanie języka okna po zmianie preferencji. Skrót w menu Start zawiera teraz nazwę PoloQP File Organizer oraz polską nazwę programu, co ułatwia wyszukiwanie.

Gotowy [instalator testowy 1.3.1](https://github.com/polokR1/file-organizer-releases/releases/tag/windows-v1.3.1) jest publikowany w osobnym repozytorium wydań. [Zapis kontroli źródeł i instalatora](RELEASE-1.3.1.md) wyjaśnia pochodzenie tej wersji oraz zakres weryfikacji.

Pierwotny skrypt sortujący zachowano w repozytorium jako prototyp. Aktualny program uruchamia się z `organizer.py`.
