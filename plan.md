# План улучшений

Каждый пункт выполняется отдельным коммитом. После каждого пункта запускаются
тесты и проверки Ruff/ty.

1. [ ] Защитить публикацию от дублей страниц и незаметных ошибок загрузки attachments.
2. [ ] Безопасно экранировать CDATA в code и PlantUML блоках.
3. [ ] Поддержать ordered checkbox lists в Confluence tasks.
4. [ ] Улучшить рекурсивный поиск изображений и устранить коллизии имён attachments.
5. [ ] Валидировать JSON API-ответы, page ID и ключ `confluence_url` во frontmatter.
6. [ ] Рендерить многострочные Canvas group/edge labels.
7. [ ] Укрепить CI и release workflow: matrix Python, проверка версии tag, wheel smoke test,
   чистые artifacts и явные permissions.
8. [ ] Обновить README и добавить CHANGELOG.
9. [ ] Выполнить полный набор проверок, создать `v0.1.11`, push и GitHub Release.
