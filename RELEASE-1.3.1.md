# File Organizer 1.3.1 — kontrola źródeł

Kontrola z 25 listopada 2026 r. uzupełnia źródła istniejącej wersji 1.3.1. Nie tworzy nowego wydania ani nie zastępuje opublikowanego instalatora.

## Źródła i poprawki

Publiczna gałąź `main` kończyła się na commicie `24752dba3ca7088e35213723639653186c345432` z wersją 1.3.0. Zachowane lokalnie źródła 1.3.1 różniły się od tego commitu w czterech plikach:

- `app_version.py`: istniejący numer 1.3.1.
- `organizer_preferences_ui.py`: obsługa argumentu `language`, który był już przekazywany przez okno główne; osobna zmienna dla pola wyboru języka. Pozwala to otworzyć Ustawienia i zachować bieżący język interfejsu do ponownego uruchomienia.
- `installer/setup.nsi`: skrót `PoloQP File Organizer - Organizator plików`, jawny kontekst bieżącego użytkownika oraz usuwanie starego skrótu po utworzeniu nowego.
- `tests/test_preferences_main_ui.py`: test rzeczywistego otwierania i zapisywania Ustawień, ponownego otwarcia oraz odtworzenia preferencji po restarcie okna.

W tym uzupełnieniu zaktualizowano też README i dodano niniejszy zapis kontroli. Oryginalny lokalny katalog roboczy pozostawiono bez zmian; porównania i budowę wykonano w osobnym klonie.

## Opublikowany plik

[Wydanie testowe Windows 1.3.1](https://github.com/polokR1/file-organizer-releases/releases/tag/windows-v1.3.1) opublikowano 19 listopada 2026 r.

| Pole | Potwierdzona wartość |
| --- | --- |
| Plik | `FileOrganizer-Setup-x64-1.3.1.exe` |
| Rozmiar | 14 368 027 bajtów |
| SHA-256 | `2296b776f320a83188c51f1c40b8d41f842c9e4551f4992229cf714a5925035d` |
| ProductVersion / FileVersion | `1.3.1` / `1.3.1` |
| Podpis Authenticode | `NotSigned` |

Lokalnie zachowany instalator ma dokładnie ten rozmiar i SHA-256, który GitHub zwraca dla opublikowanego zasobu. Odczytano też jego metadane wersji i podpisu.

Kod obiektów Pythona z zachowanego lokalnego pakietu PyInstaller 1.3.1 porównano z kompilacją wszystkich 13 obecnych w nim modułów aplikacji, w tym `organizer.py`, oraz z osobnym punktem wejścia `organizer`. Po normalizacji nazw ścieżek kod jest zgodny. To dodatkowy dowód zgodności odzyskanych źródeł z archiwalną aplikacją.

Nie ma historycznego manifestu, który kryptograficznie wiązałby commit źródeł z opublikowanym instalatorem. Sama zgodność lokalnego instalatora z GitHub oraz kodu z zachowanym pakietem nie dowodzi w pełni tej relacji. Ponowna budowa służy kontroli możliwości budowania; nie jest deklaracją odtwarzalności bajtowej starego instalatora.

## Weryfikacja

- Windows 11 x64, CPython 3.14.0, Pillow 13.0.0, PyInstaller 6.22.3, NSIS 3.13.
- `python -m unittest discover -s tests -v`: 107 testów, 104 zaliczone, 3 pominięte z powodu braku uprawnień środowiska do tworzenia dowiązań twardych i symbolicznych; bez błędów.
- Test regresji Ustawień na starej implementacji odtworzył `TypeError: show_preferences() got an unexpected keyword argument 'language'`. Po przywróceniu implementacji 1.3.1 oba testy w `test_preferences_main_ui.py` przeszły.
- Świeży pakiet aplikacji PyInstaller oraz oba warianty NSIS (zwykły i `SMOKE_TEST`) zbudowały się poprawnie. Kompilator NSIS uruchomiono poza ograniczeniem środowiska, które powodowało błąd relokacji DLL przy starcie istniejącego kompilatora.
- Wariant `SMOKE_TEST` zainstalowano i odinstalowano w nowym, wydzielonym folderze kontroli. Skróty i rejestr właściwej instalacji nie są zmieniane przez ten wariant. Zainstalowany EXE miał ten sam SHA-256 co świeży pakiet. Deinstalacja usunęła pliki aplikacji i zachowała dodany testowy plik użytkownika.

Nie przeprowadzono w tej kontroli ponownej instalacji programu w profilu użytkownika ani ręcznego testu wyszukiwarki menu Start. Oryginalne wydanie i jego zasoby pozostają bez zmian.
