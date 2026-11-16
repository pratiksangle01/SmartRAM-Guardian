# 🐬 SmartRAM Guardian

## Linux

```sh
curl -fsSL https://raw.githubusercontent.com/pratiksangle01/SmartRAM-Guardian/main/install.sh | sh
```

## macOS

```sh
curl -fsSL https://raw.githubusercontent.com/pratiksangle01/SmartRAM-Guardian/main/install.sh | sh
```

## Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/pratiksangle01/SmartRAM-Guardian/main/install.ps1 | iex
```

## Git

```sh
git clone https://github.com/pratiksangle01/SmartRAM-Guardian.git
cd SmartRAM-Guardian
python -m pip install .
python -m smartram start
```

Update:

```sh
git pull
python -m pip install --upgrade .
```

## Commands

```sh
smartram status          # RAM saved, what was closed, what was kept
smartram start           # run in the background
smartram stop
smartram pause
smartram resume
smartram allow <app>     # also close this app when it idles in the background
smartram protect <app>   # never close this app
smartram enable          # start at login
smartram disable
smartram uninstall
```
