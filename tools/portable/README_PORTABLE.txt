WWB Scanner - portable version (Windows 10/11, 64-bit)
=======================================================

Runs without installing Python. Unpack the folder anywhere you can write to
(a USB stick, Documents, the Desktop) and start wwb_scanner.exe.

The dongle needs the WinUSB driver once per PC
----------------------------------------------
1. Plug in the RTL-SDR dongle.
2. Download Zadig: https://zadig.akeo.ie
3. Options > List All Devices; choose "Bulk-In, Interface (Interface 0)"
   (or "RTL2838UHIDIR"); driver WinUSB; Replace Driver.
Skip this if SDR# / SDR++ / an earlier install already works on this PC.

Where the data are
------------------
Everything the program writes stays in the "data" folder next to the exe:
scan history, calibrations of each dongle, window settings, logs. Take the
whole folder with you to keep them. (If the folder can not be written to,
for example under C:\Program Files, the data go to
%USERPROFILE%\wwb_scanner_data instead.)

The first start
---------------
Windows may say "Windows protected your PC": the exe is not signed.
Click "More info" > "Run anyway".
A check without scanning:  wwb_scanner.exe --selftest
(report: data\logs\selftest.txt). Program output: data\logs\console.txt,
log: data\logs\wwb_scanner.log.

Two dongles with the same serial number
---------------------------------------
Many dongles (the RTL-SDR Blog V4 too) have the serial number 00000001.
To tell two apart, give each its own number, one dongle plugged in at a time:
    rtl-tools\rtl_eeprom.exe -s 00000002
then unplug and plug it in again.

Update
------
Unpack the new version next to the old one and move the "data" folder into it.

------------------------------------------------------------------------
WWB Scanner - портативная версия (Windows 10/11, 64 бит)

Работает без установки Python. Распакуйте папку туда, где есть право записи
(флешка, Документы, Рабочий стол), и запустите wwb_scanner.exe.

Драйвер WinUSB для донгла - один раз на каждом ПК
- Подключите донгл.
- Скачайте Zadig: https://zadig.akeo.ie
- Options > List All Devices; выберите "Bulk-In, Interface (Interface 0)"
  (или "RTL2838UHIDIR"); драйвер WinUSB; Replace Driver.
Не нужно, если на этом ПК уже работают SDR# / SDR++ / прежняя установка.

Данные
Всё, что пишет программа, лежит в папке "data" рядом с exe: история сканов,
калибровки каждого донгла, настройки окна, журналы. Чтобы взять их с собой,
переносите папку целиком. Если в папку писать нельзя (C:\Program Files),
данные пойдут в %USERPROFILE%\wwb_scanner_data.

Первый запуск
Windows может написать "Windows защитила ваш компьютер": exe не подписан.
Нажмите "Подробнее" > "Выполнить в любом случае".
Проверка без скана:  wwb_scanner.exe --selftest  (отчёт data\logs\selftest.txt).

Два донгла с одинаковым серийным номером
У многих донглов (и у V4) номер 00000001. Чтобы их различать, запишите каждому
свой номер, подключая по одному:  rtl-tools\rtl_eeprom.exe -s 00000002
затем переподключите донгл.

Обновление
Распакуйте новую версию рядом со старой и перенесите в неё папку "data".
