import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

# Funkcja do wyboru folderu źródłowego
def choose_source_folder():
    folder_selected = filedialog.askdirectory()
    source_folder_entry.delete(0, tk.END)
    source_folder_entry.insert(0, folder_selected)

# Funkcja do wyboru folderu docelowego
def choose_destination_folder():
    folder_selected = filedialog.askdirectory()
    destination_folder_entry.delete(0, tk.END)
    destination_folder_entry.insert(0, folder_selected)

# Funkcja do generowania unikalnej nazwy pliku w przypadku duplikatów
def get_unique_filename(destination, file_name):
    base_name, extension = os.path.splitext(file_name)
    counter = 1
    new_file_name = file_name
    
    # Dopóki plik o tej nazwie istnieje, dodajemy licznik do nazwy
    while os.path.exists(os.path.join(destination, new_file_name)):
        new_file_name = f"{base_name}_{counter}{extension}"
        counter += 1
    
    return new_file_name

# Funkcja do przenoszenia plików
def move_files():
    source = source_folder_entry.get()
    destination = destination_folder_entry.get()

    # Sprawdzanie, czy foldery istnieją
    if not os.path.exists(source):
        messagebox.showerror("Błąd", "Folder źródłowy nie istnieje.")
        return
    if not os.path.exists(destination):
        messagebox.showerror("Błąd", "Folder docelowy nie istnieje.")
        return

    # Pobieranie typu plików
    file_type = file_type_entry.get().lower()

    # Dodanie '.' jeśli użytkownik nie wpisał go na początku
    if not file_type.startswith('.'):
        file_type = '.' + file_type

    # Flaga do sprawdzenia, czy pliki zostały przeniesione
    files_moved = False

    try:
        # Przeszukiwanie folderu źródłowego wraz z podfolderami
        print(f"Przeszukiwanie folderu: {source}")  # Debug
        for dirpath, dirnames, filenames in os.walk(source):
            print(f"Obecny folder: {dirpath}")  # Debug
            print(f"Znalezione pliki: {filenames}")  # Debug
            for file_name in filenames:
                if file_name.endswith(file_type):
                    full_file_name = os.path.join(dirpath, file_name)
                    new_file_name = get_unique_filename(destination, file_name)  # Nowa, unikalna nazwa
                    destination_path = os.path.join(destination, new_file_name)
                    shutil.move(full_file_name, destination_path)
                    files_moved = True
                    print(f"Przeniesiono plik: {full_file_name} -> {destination_path}")  # Debug

        if files_moved:
            messagebox.showinfo("Sukces", "Pliki zostały przeniesione.")
        else:
            messagebox.showwarning("Brak plików", f"Nie znaleziono plików typu {file_type} w folderze źródłowym.")
    except Exception as e:
        messagebox.showerror("Błąd", f"Coś poszło nie tak: {str(e)}")

# Tworzenie głównego okna aplikacji
root = tk.Tk()
root.title("Przenoszenie plików")

# Etykiety i pola do wpisania ścieżek
tk.Label(root, text="Folder źródłowy:").grid(row=0, column=0, padx=10, pady=5)
source_folder_entry = tk.Entry(root, width=50)
source_folder_entry.grid(row=0, column=1, padx=10, pady=5)

choose_source_button = tk.Button(root, text="Wybierz", command=choose_source_folder)
choose_source_button.grid(row=0, column=2, padx=10, pady=5)

tk.Label(root, text="Folder docelowy:").grid(row=1, column=0, padx=10, pady=5)
destination_folder_entry = tk.Entry(root, width=50)
destination_folder_entry.grid(row=1, column=1, padx=10, pady=5)

choose_destination_button = tk.Button(root, text="Wybierz", command=choose_destination_folder)
choose_destination_button.grid(row=1, column=2, padx=10, pady=5)

# Pole do wpisania rozszerzenia pliku
tk.Label(root, text="Typ pliku (np. .jpg, .txt):").grid(row=2, column=0, padx=10, pady=5)
file_type_entry = tk.Entry(root, width=50)
file_type_entry.grid(row=2, column=1, padx=10, pady=5)

# Przycisk do przenoszenia plików
move_button = tk.Button(root, text="Przenieś pliki", command=move_files)
move_button.grid(row=3, column=1, padx=10, pady=20)

# Start aplikacji
root.mainloop()
