//    Copyright 2026 Genesis Corporation.
//    Licensed under the Apache License, Version 2.0 (the "License")
// Rewrite internal links to Russian versions on Russian pages.
(function () {
  var navTranslations = {
    "Software": "Продукт",
    "Usage": "Использование",
    "Platform Overview": "Обзор платформы",
    "Licensing": "Лицензирование",
    "Local Deployment": "Локальное развёртывание",
    "Admin Guide": "Руководство администратора",
    "Support": "Поддержка",
    "Security": "Безопасность",
    "Troubleshooting": "Устранение неполадок",
    "Wizard": "Мастер настройки",
    "Developers": "Разработчикам",
    "App Guide": "Руководство по приложениям",
    "Core Guide": "Руководство по ядру",
    "Repo Proxy": "Repo Proxy",
    "Element": "Элементы",
    "Manifest": "Манифест",
    "Example commands": "Примеры команд",
    "IAM": "IAM",
    "Permissions": "Права доступа",
    "Network": "Сеть",
    "Secrets": "Секреты",
    "Certificates": "Сертификаты",
    "Passwords": "Пароли",
  };

  function isRussianPage() {
    return window.location.pathname.includes(".ru.") ||
           window.location.pathname.includes("/ru/");
  }

  function rewriteToRussian(url) {
    if (!url) return url;
    // Skip external URLs, anchors, assets, and already-Russian links
    if (
      url.startsWith("http") ||
      url.startsWith("#") ||
      url.includes(".ru.") ||
      url.includes("assets/") ||
      url.startsWith("javascript:")
    ) {
      return url;
    }
    // Rewrite .html to .ru.html for internal links
    if (url.endsWith(".html")) {
      return url.replace(/\.html$/, ".ru.html");
    }
    return url;
  }

  function fixLinks() {
    if (!isRussianPage()) return;

    // Fix all internal links: nav, tabs, content
    document.querySelectorAll("a[href]").forEach(function (el) {
      var href = el.getAttribute("href");
      var newHref = rewriteToRussian(href);
      if (newHref !== href) {
        el.setAttribute("href", newHref);
      }
    });

    // Translate nav labels in sidebar, tabs, and titles
    document.querySelectorAll(".md-nav__title, .md-nav__link, .md-tabs__link").forEach(function (el) {
      var text = el.textContent.trim();
      if (navTranslations[text]) {
        // Only replace text nodes, preserve icons/HTML
        var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, null);
        var node;
        while ((node = walker.nextNode())) {
          var t = node.textContent.trim();
          if (navTranslations[t]) {
            node.textContent = node.textContent.replace(t, navTranslations[t]);
          }
        }
      }
    });

    // Mark active nav items (Zensical doesn't set active classes on ru pages)
    var fullPath = window.location.pathname.replace(/^.*\/exordos\//, "");
    // Determine directory prefix for relative path resolution
    var pathParts = fullPath.split("/");
    var currentFile = pathParts.pop();
    var currentDir = pathParts.length > 0 ? pathParts.join("/") + "/" : "";
    var navLinks = document.querySelectorAll(".md-nav--primary .md-nav__link");
    navLinks.forEach(function (link) {
      var href = link.getAttribute("href");
      if (href) {
        // Links are already rewritten to .ru.html by fixLinks above
        // Resolve relative path to full path for comparison
        var normalizedHref = href.replace(/^\.\//, "");
        if (normalizedHref.startsWith("../")) {
          // Parent directory: strip ../ and compare
          normalizedHref = normalizedHref.replace(/^\.\.\//, "");
        } else if (!normalizedHref.includes("/")) {
          // Same directory: prepend current dir
          normalizedHref = currentDir + normalizedHref;
        }
        if (normalizedHref === fullPath) {
          // Mark the link itself as active (for blue highlighting)
          link.classList.add("md-nav__link--active");
          // Mark the link's item as active
          var item = link.closest(".md-nav__item");
          if (item) {
            item.classList.add("md-nav__item--active");
          }
          // Mark the parent section as active and check its toggle
          var nestedItem = link.closest(".md-nav__item--nested");
          if (nestedItem) {
            nestedItem.classList.add("md-nav__item--active");
            var toggle = nestedItem.querySelector(":scope > .md-nav__toggle");
            if (toggle) {
              toggle.checked = true;
            }
          }
        }
      }
    });
  }

  // Run on initial load
  document.addEventListener("DOMContentLoaded", fixLinks);

  // Run on Zensical/Material instant page transitions
  document.addEventListener("DOMContentSwitch", fixLinks);
})();
