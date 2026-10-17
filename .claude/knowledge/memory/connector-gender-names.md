---
name: connector-gender-names
description: Connector halves are named the Russian way — вилка = pins, розетка = sockets — in every series, not by the maker's plug/receptacle
metadata:
  type: feedback
---
Название половин разъёма следует контактам: вилка — часть со штырями (pin), розетка — часть с гнёздами (socket), во всех сериях, независимо от английских «plug / receptacle» производителя.

**Why:** владелец 17.10 «давай по-русски»; в магазинных объявлениях SMR называют «вилка папа». JST называет SMR (штыри) receptacle, ERP это повторяла и выходила наоборот (SM, RCY, EL).

**How to apply:** слово берётся из `contacts` в `connector_housings.MATING` через `gender_title` / `GENDER_WORD` (единственное место); названия семейств и секций переписываются сами, вручную писать «вилка/розетка» там, где контакты известны, не нужно. Где контакты неизвестны (нет листа), название оставить как есть. Тест: `test_every_housing_with_known_contacts_is_named_by_them…` в `backend/tests/techcards/test_connector_series.py`. Артикулы не переименовывать.
