---
name: staff-and-sites
description: Staff, roles and shop sites (ЦАР / ЖУК) — who is master, full-access roles, PIN login, no test staff
metadata:
  type: project
---

- Login by name + PIN (`import_staff`, PR #87). SUPPLYMAP PINs are hashed, so everyone gets a NEW PIN; `import_staff --apply` prints them on the production DB only (not from the cloud).
- Full access always (owner): технолог, специалист по снабжению, директор производства, генеральный директор (PR #81). Their `site` is empty.
- Fitters: role `staff`, department «Монтаж», site ЦАР (default) or ЖУК (`identity_users.site`, PR #78). A master sees only his site.
- ЖУК: master Степан; Зингер Антон, Наумов Александр, Евсеев Антон, Безменов Никита.
- ЦАР: Рыбкина Ольга, Епифанов Илья, Попов Игнат, Богородская Элина, Макарова Полина, Наумова Елена, Пузанов Александр, Думанская Анна, Шилов Артем.
- No test staff. «Вася Пупкин» is disabled as a test record (`drop_test_staff`, PR #61).

Used by: [[module-production]]. Hub: [[erp-next-hub]].
