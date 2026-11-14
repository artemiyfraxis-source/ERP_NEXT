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
`T.SMOothing.STATe = False`, `T.LIMit.DATA = [1,1,f1,f2,1.3,1.3]` + `T.LIMit.DISPlay.STATe = True`,
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
