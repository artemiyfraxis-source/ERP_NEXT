# S2VNA — проверка COM-команд на рабочем ПК (14.11.2026)

Прибор: Planar S50180 №26030095, S2VNA 26.2.2, объект `S2VNA.Application` (без настройки SCPI-сокета).
Проверял Claude (main) на ПК владельца. Прибор в этот момент был отключён от USB (`Ready = False`):
настройки программы читаются и пишутся и без него; снятие мер и трасс — отдельной проверкой с подключённым прибором.

Обозначения: `S = app.SCPI`, `C = S.SENSe(1)`, `T = S.CALCulate(1).SELected`.

## Чтение — работает

| Что | Вызов | Значение на ПК |
|---|---|---|
| Макс. точек | `S.SERVice.SWEep.POINts` | 200001 |
| Диапазон прибора | `S.SERVice.SWEep.FREQuency.MINimum / MAXimum` | 100 кГц / 18 ГГц |
| Вид развёртки, точки | `C.SWEep.TYPE`, `C.SWEep.POINts` | LIN, 201 |
| Полоса ПЧ | `C.BANDwidth.RESolution` | 10000 |
| Усреднение | `C.AVERage.STATe` | False |
| Мощность порта 1 | `S.SOURce(1).POWer.PORT(1).LEVel.IMMediate.AMPLitude` | 0 дБм |
| Трасс, формат, сглаживание | `S.CALCulate(1).PARameter(1).COUNt`, `T.FORMat`, `T.SMOothing.STATe` | 1, MLOG, False |
| Коррекция | `C.CORRection.STATe`, `T.CORRection.STATus` | False, пусто |
| Метод калибровки, набор | `C.CORRection.COLLect.METHod.TYPE`, `…CKIT.LABel` | RESPT, 85032F |
| Запуск | `S.TRIGger.SEQuence.SOURce`, `S.INITiate(1).CONTinuous` | INT, True |
| Импеданс | `C.CORRection.IMPedance.INPut.MAGNitude` | 50 |
| Набор мер | `…CKIT.SELect = 28; …CKIT.STAN(i).TYPE/LABel/C0…L3/Delay/LOSS/Z0` | 6650F27, 7 мер |

## Запись с проверкой обратным чтением — работает

`C.SWEep.POINts = 1601`, `C.FREQuency.STARt/STOP`, `C.BANDwidth.RESolution = 1000`, `T.FORMat = "SWR"`,
`T.SMOothing.STATe = False`, `T.LIMit.DATA` (линия нормы СИ) + `T.LIMit.DISPlay.STATe = True`,
`T.MATH.FUNCtion = "OFF"`, `T.FILTer.GATE.TIME.STATe = False`, `S.CALCulate(1).FSIMulator.STATe = False`,
`…CKIT.SELect = 28` (и возврат прежнего номера).

## Сохранение и восстановление состояния оператора — работает

`S.MMEMory.STORe.STYPe = "CDST"` → `S.MMEMory.STORe.STATe = <файл .sta>` → изменения → `S.MMEMory.LOAD.STATe = <файл>`:
после загрузки точки, IFBW, формат, диапазон и выбранный набор вернулись к исходным (201 / 10000 / MLOG / 18 ГГц / 85032F).

## Ещё не проверено (нужен подключённый прибор и меры)

- `K.METHod.SOLT1/SOLT2`, `K.ACQuire.OPEN/SHORt/LOAD/THRU`, `K.SAVE()` и статус `COR` после калибровки.
- Однократная развёртка `S.INITiate(1).IMMediate()` + `S.IEEE4882.OPC`, чтение `S.CALCulate(1).TRACe(t).DATA.SDATa` по нескольким трассам.
- Калибровка АКМ ACM2520-111 (команды модуля автокалибровки — по руководству, раздел АКМ).
- Отключение USB во время сессии и восстановление.

## Проверка с прибором и АКМ — 16.11.2026

Planar S50180 №26030095 по USB, АКМ ACM2520-111 №26140008 (оба порта 3,5 мм розетка), тракт: порты → кабели
C50SMNM.2 (концы SMA вилка). Профиль: 10 МГц – 18 ГГц, 1601 точка, ПЧ 1 кГц, 0 дБм, трассы S11 (SWR), S21, S12 (MLOG), S22 (SWR).

| Что | Результат |
|---|---|
| Сведения об АКМ | `K.ECAL.INFormation` → `ACM2520.2,26140008,…,100 kHz,20 GHz,1601,…,3.5mm -F-,3.5mm -F-` |
| Калибровка АКМ | `K.ECAL.ORIentation.STATe = True`; `K.ECAL.SOLT2 = [1,2]` — одна блокирующая команда, 21 с |
| Признак калибровки | у каждой трассы (`PARameter(t).SELect()`) `SELected.CORRection.STATus` = `COR`. `K.METHod.TYPE` после АКМ остаётся `RESPT` — не признак |
| Разовая развёртка | `INITiate(1).IMMediate()` возвращается сразу, не дожидаясь развёртки. Рабочая последовательность: `INITiate(1).CONTinuous = True`, `TRIGger.SEQuence.SOURce = "BUS"`, `TRIGger.SEQuence.SINGle()`, чтение `IEEE4882.OPC` — 3,9 с |
| Повторяемость | два замера подряд расходятся на ≤ 0,001 по КСВН |
| Контроль АКМ эталоном | нагрузка LOAD (розетка) из 6650F27: порт 1 — КСВН макс. 1,023, порт 2 — 1,021 до 18 ГГц; развязка портов −129…−137 дБ |
| Перемычка ADP1A 35F-35F | СИ.008 N8B139: КСВН 1,044/1,051, 0,145 дБ; СИ.009 N8B140: 1,022/1,023, 0,143 дБ |
| ADP1A 35M-35F в паре с N8B140 | СИ.006 N1C765: 1,054/1,050, 0,26 дБ; СИ.007 N1C766: 1,061/1,058, 0,27 дБ |

Права: COM к S2VNA — только из обычного (не повышенного) сеанса пользователя. Клиент с правами администратора
получает отдельный экземпляр S2VNA без прибора (`Ready = False`, пустой серийный номер). Агент ERP работает без
повышения — так и оставить.

Ещё не проверено: калибровка SOLT набором 6650F27 по шагам (`ACQuire.OPEN/SHORt/LOAD/THRU`, `SAVE`), отключение USB
во время сессии.
