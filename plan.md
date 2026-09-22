# План улучшений

Каждый пункт выполняется отдельным коммитом. После каждого пункта запускаются
тесты и проверки Ruff/ty.

1. [x] Защитить публикацию от дублей страниц и незаметных ошибок загрузки attachments.
2. [x] Безопасно экранировать CDATA в code и PlantUML блоках.
3. [x] Поддержать ordered checkbox lists в Confluence tasks.
4. [x] Улучшить рекурсивный поиск изображений и устранить коллизии имён attachments.
5. [x] Валидировать JSON API-ответы, page ID и ключ `confluence_url` во frontmatter.
6. [x] Рендерить многострочные Canvas group/edge labels.
7. [x] Укрепить CI и release workflow: matrix Python, проверка версии tag, wheel smoke test,
   чистые artifacts и явные permissions.
8. [x] Обновить README и добавить CHANGELOG.
9. [ ] Выполнить полный набор проверок, создать `v0.1.11`, push и GitHub Release.
