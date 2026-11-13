rtlsdr.dll scanner-v7 — драйвер RTL-SDR Blog V4 для сканера (сборка 13.11.2026)
==========================================================================

СТАТУС: собрано, проверено на модели USB (ASan/UBSan) и в Wine с Windows Python 3.13
и pyrtlsdr 0.5.1 (загрузка, все функции, установка). На реальном V4 ещё НЕ проверено.
Предыдущая сборка scanner-v6 проверена на вашем V4 13.11 (qa_pc: 0 FAIL).

Что это
-------
scanner-v6 плюс патч 0009 — функции, без которых новый pyrtlsdr (0.4 и выше;
0.5.1 работает на Python 3.10+) не загружается:
- rtlsdr_set_dithering — PLL dithering тюнера R820T/R828D. По умолчанию включён,
  как и раньше; pyrtlsdr вызывает её с 1 при открытии донгла — регистры не меняются.
- rtlsdr_set_gpio_output / _input / _bit, rtlsdr_get_gpio_bit, rtlsdr_set_gpio_byte,
  rtlsdr_get_gpio_byte, rtlsdr_set_gpio_status — GPIO микросхемы RTL2832U, те же
  имена и смысл, что в форке librtlsdr, на который рассчитан pyrtlsdr. Сканер их не
  вызывает. Осторожно: вывод 0 управляет bias-tee (питание на антенный вход).
В обычной работе драйвер делает то же, что v6: все сценарии модели v6 дают те же
результаты; единственное отличие — закрытие выдернутого донгла: 3 обмена USB вместо 5.
Функций стало 51 (43 + 8). Из того, что знает pyrtlsdr 0.5.1, нет только
rtlsdr_set_and_get_tuner_bandwidth — pyrtlsdr считает её необязательной.

Сборка: source\build_all.sh (rtl-sdr-blog aed0ea1 + патчи 0001-0009, libusb v1.0.29);
две сборки в разных папках дали побитно одинаковые файлы (source\BUILD_INFO.txt).

Установка
---------
Сканер закрыт. Из этой папки, тем Python, в котором работает программа:
  python install_dll.py            установить
  python install_dll.py where      какая DLL загружается
  python install_dll.py rollback   вернуть предыдущую (копия <имя>.before-v7)

- Python 3.7 (pyrtlsdr 0.3): заменяет ...\Python37\Scripts\librtlsdr.dll, как раньше.
- Новое окружение Python 3.10+ (pyrtlsdr 0.5): если DLL ещё нет или там старая, без
  нужных функций, — ставит <окружение>\Scripts\rtlsdr.dll. Python 3.8+ сам не ищет DLL
  в этой папке; программа V21.3+ (wwb_scanner\dllpath.py) и install_dll.py добавляют
  её в поиск.
Ожидается «OK - scanner-v7 installed».

Проверка на донгле
------------------
1. Python 3.7: python qa_pc.py --smoke — PC5 «scanner-v7 x64», сканы как с v6.
2. Новое окружение: см. инструкцию к V21.3 (claude/instrukciya-v213.md в проекте).

Модель (source\model, model_results.txt)
-----------------------------------------
T14 dithering: включение — 0 обменов USB, бит 4 регистра 0x12 = 0; выключение —
бит 4 = 1 после перестройки, частота верна (62 Гц); снова включение — 0.
T15 GPIO: вывод 3 как выход и 1 — GPO 08, GPOE 08; чтение 1; вывод 2 как вход читает
внешний уровень; ошибочный номер вывода и NULL — -1.
T1-T13 (v4-v6): те же результаты, что у v6. ASan/UBSan: замечаний нет.

Не проверено
------------
- на реальном V4 и на настоящем Windows: только модель и Wine;
- Wine в песочнице не запускает numpy (нет функции ucrtbase.crealf), поэтому полный
  pyrtlsdr с numpy под Windows проверен не был — только загрузка DLL и все привязки.

Лицензия
--------
rtl-sdr: GPL-2.0-or-later (source\COPYING.rtl-sdr.GPL-2.0). libusb 1.0.29 (LGPL-2.1),
статически. Исходники: github.com/rtlsdrblog/rtl-sdr-blog aed0ea1 + source\patches.
