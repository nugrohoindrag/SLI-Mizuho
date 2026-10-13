loadSidebarMenu(); // langsung jalan

function loadSidebarMenu() {
  const $target = $(".sidebar-dynamic-wrapper");

  if ($target.length === 0) {
    console.warn("❌ Target .sidebar-dynamic-wrapper tidak ditemukan.");
    return;
  }

  // console.log("🔍 SidebarMenu: Checking localStorage cache...");

  const STORAGE_KEY = "sidebar_menu_cache";
  const isForcePasswordPage = document.body?.dataset?.forcePasswordChange === "true";

  function hideSidebarMenu() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      console.warn("⚠️ Gagal membersihkan cache menu:", error);
    }
    $target.empty();
  }

  if (isForcePasswordPage) {
    hideSidebarMenu();
    return;
  }

  function saveToLocalStorage(key, value, hours) {
    try {
      // console.log(`📝 Menyimpan cache ke localStorage: ${key}`);
      const expires = new Date(Date.now() + hours * 60 * 60 * 1000).getTime();
      
      const item = {
        value: value,
        expiry: expires
      };
      
      localStorage.setItem(key, JSON.stringify(item));
      return true;
    } catch (error) {
      console.error("❌ Gagal menyimpan ke localStorage:", error);
      return false;
    }
  }

  function getFromLocalStorage(key) {
    try {
      const itemStr = localStorage.getItem(key);
      
      // Return null if item doesn't exist
      if (!itemStr) {
        return null;
      }
      
      const item = JSON.parse(itemStr);
      const now = new Date().getTime();
      
      // Check if the item has expired
      if (now > item.expiry) {
        // If expired, remove the item from storage
        localStorage.removeItem(key);
        return null;
      }
      
      return item.value;
    } catch (error) {
      console.error("❌ Gagal membaca dari localStorage:", error);
      return null;
    }
  }

  const cached = getFromLocalStorage(STORAGE_KEY);

  if (cached) {
    try {
      // console.log("📦 Memuat sidebar dari localStorage cache.");
      const data = JSON.parse(cached);
      renderSidebar(data);
      return;
    } catch (err) {
      console.warn("⚠️ Gagal parsing cache, akan fetch ulang dari API.");
    }
  }

  // Fetch jika cache tidak tersedia
  // console.log("🌐 SidebarMenu: Fetching from /api/menu/");
  $target.html('<div class="text-center p-2 text-muted"><em>Loading menu...</em></div>');

  fetch("/api/menu/", { credentials: "include" })
    .then(res => {
      return res.json().catch(() => ({})).then(body => {
        if (body && body.force_password_change) {
          hideSidebarMenu();
          return null;
        }
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        return body;
      });
    })
    .then(data => {
      if (!data) return;
      saveToLocalStorage(STORAGE_KEY, JSON.stringify(data), 1); // Simpan 1 jam
      renderSidebar(data);
    })
    .catch(error => {
      console.error("❌ Gagal memuat menu:", error);
      $target.html('<div class="text-danger p-2">Gagal memuat menu.</div>');
    });

  function renderSidebar(data) {
    const pathname = window.location.pathname;
    const menuMap = {};
    const rootGroups = {};
    const noRootMenus = [];
    const footerMenus = [];

    function hasActiveMenu(menu, pathname) {
      if (pathname === menu.link) return true;
      if (Array.isArray(menu.children)) {
        return menu.children.some(child => hasActiveMenu(child, pathname));
      }
      return false;
    }

    data.forEach(menu => {
      menu.children = [];
      menuMap[menu.id] = menu;

      const menuName = menu.name?.toLowerCase();
      if (menuName === 'contact us' || menuName === 'faq') {
        footerMenus.push(menu);
        return;
      }

      if (menu.menu_root) {
        const rootName = menu.menu_root;
        if (!rootGroups[rootName]) rootGroups[rootName] = [];
        rootGroups[rootName].push(menu);
      } else {
        noRootMenus.push(menu);
      }
    });

    Object.values(menuMap).forEach(menu => {
      if (menu.parent_id) {
        const parent = menuMap[menu.parent_id];
        if (parent) {
          parent.children.push(menu);
        }
      }
    });

    function buildMenuHTML(menus, pathname = window.location.pathname) {
      return menus
        .filter(menu => !menu.parent_id)
        .map(menu => {
          const isActive = pathname === menu.link;
          const hasChildren = Array.isArray(menu.children) && menu.children.length > 0;
          const icon = menu.icon_html
            || (menu.icon_css ? `<i class="${menu.icon_css}"></i>` : '');

          if (hasChildren) {
            const isChildActive = menu.children.some(child => pathname === child.link);
            return `
              <li class="nav-item has-sub ${isChildActive ? 'open' : ''}">
                <a class="nav-link d-flex align-items-center" href="javascript:void(0);">
                  ${icon}
                  <span class="menu-title">${menu.name}</span>
                </a>
                <ul class="menu-content" ${isChildActive ? 'style="display: block;"' : ''}>
                  ${buildMenuHTML(menu.children, pathname)}
                </ul>
              </li>
            `;
          } else {
            return `
              <li class="nav-item ${isActive ? 'active' : ''}">
                <a class="nav-link d-flex align-items-center ${isActive ? 'active' : ''}" href="${menu.link || 'javascript:void(0);'}">
                  ${icon}
                  <span class="menu-title">${menu.name}</span>
                </a>
              </li>
            `;
          }
        })
        .join('');
    }

    let finalHTML = '';

    if (noRootMenus.length) {
      finalHTML += buildMenuHTML(noRootMenus);
    }

    for (const [rootName, menus] of Object.entries(rootGroups)) {
      const isAnyMenuActive = menus.some(menu => hasActiveMenu(menu, pathname));
      const collapseClass = isAnyMenuActive ? 'show' : '';
      const collapsedClass = isAnyMenuActive ? '' : 'collapsed';
      const ariaExpanded = isAnyMenuActive ? 'true' : 'false';
      const collapseId = `menuRoot-${rootName.replace(/\s+/g, '-')}`;

      finalHTML += `
        <li class="nav-item">
          <a class="nav-link d-flex justify-content-between align-items-center ${collapsedClass}" data-toggle="collapse" href="#${collapseId}" role="button" aria-expanded="${ariaExpanded}" aria-controls="${collapseId}">
            <span>${rootName}</span>
          </a>
          <ul class="menu-content" id="${collapseId}" style="${isAnyMenuActive ? 'display: block;' : 'display: none;'}">
            ${buildMenuHTML(menus)}
          </ul>
        </li>
      `;
    }

    if (footerMenus.length > 0) {
      finalHTML += '<div class="sidebar-footer-menus mt-auto">';
      finalHTML += buildMenuHTML(footerMenus);
      finalHTML += '</div>';
    }

    $target.html(finalHTML);

    const $activeLink = $target.find("a.nav-link.active");
    if ($activeLink.length) {
      setTimeout(() => {
        const menuContent = document.querySelector('.main-menu-content');
        const activeItem = $activeLink.get(0);
        if (menuContent && activeItem) {
          const maxScrollTop = menuContent.scrollHeight - menuContent.clientHeight;
          const targetScroll = activeItem.offsetTop - menuContent.clientHeight / 2 + activeItem.clientHeight / 2;
          menuContent.scrollTop = Math.max(0, Math.min(targetScroll, maxScrollTop));
        }
      }, 200);
    }

    $target.find(".has-sub > a.nav-link").off("click").on("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      const $parent = $(this).closest(".has-sub");
      const $submenu = $parent.find("> .menu-content");
      const isOpen = $parent.hasClass("open");

      // Close siblings only at the same level to avoid collapsing unrelated branches
      const $siblings = $parent.siblings(".has-sub");
      $siblings.removeClass("open").find("> .menu-content").slideUp(200);

      if (isOpen) {
        $parent.removeClass("open");
        $submenu.stop(true, true).slideUp(200);
      } else {
        $parent.addClass("open");
        $submenu.stop(true, true).slideDown(200);
      }
    });

    // console.log("✅ Sidebar loaded from " + (cached ? "localStorage" : "API") + ".");
  }
}
