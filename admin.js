(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = { csrf: "", cloudinary: false, categories: [], page: 1, collabPage: 1, videoPage: 1, galleryPage: 1 };
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[ch]));
  const fmtDate = value => value ? new Date(value).toLocaleString() : "—";

  function toast(message, type = "ok") {
    const stack = $("#toast-stack");
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.textContent = message;
    stack.appendChild(item);
    setTimeout(() => item.remove(), 3600);
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    if (state.csrf && options.method && !["GET", "HEAD"].includes(options.method)) headers["X-CSRF-Token"] = state.csrf;
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { error: "Invalid server response" }; }
    if (!response.ok) throw new Error(data.error || data.message || `Request failed (${response.status})`);
    return data;
  }

  async function cloudinaryUpload(file, resourceType, folder) {
    if (!state.cloudinary) return null;
    let signature;
    try {
      signature = await api("/api/admin/uploads/signature", { method: "POST", body: JSON.stringify({ resource_type: resourceType, folder }) });
    } catch (error) {
      throw new Error(`Cloudinary upload signature failed: ${error.message}`);
    }
    const body = new FormData();
    body.append("file", file);
    body.append("api_key", signature.api_key);
    body.append("timestamp", signature.timestamp);
    body.append("signature", signature.signature);
    body.append("public_id", signature.public_id);
    const response = await fetch(signature.upload_url, { method: "POST", body });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.public_id) throw new Error(result.error?.message || "Cloudinary upload failed.");
    return result.public_id || signature.public_id;
  }

  function modal(html) {
    $("#modal-root").innerHTML = `<div class="modal-backdrop"><div class="modal" role="dialog">${html}</div></div>`;
    $(".close-btn", $("#modal-root")).onclick = closeModal;
    $(".modal-backdrop").onclick = event => { if (event.target.classList.contains("modal-backdrop")) closeModal(); };
  }
  function closeModal() { $("#modal-root").innerHTML = ""; }

  function loadingView() { $("#app-shell").classList.add("hidden"); $("#login-view").classList.remove("hidden"); }
  function showApp() { $("#login-view").classList.add("hidden"); $("#app-shell").classList.remove("hidden"); }

  async function boot() {
    const session = await api("/api/admin/session");
    state.csrf = session.csrf_token || "";
    state.cloudinary = Boolean(session.cloudinary);
    if (session.authenticated) { showApp(); await loadDashboard(); }
    else { loadingView(); $("#login-view").classList.remove("hidden"); }
  }

  $("#login-form").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("button", $("#login-form"));
    button.disabled = true; button.textContent = "Signing in...";
    $("#login-error").textContent = "";
    try {
      const result = await api("/api/admin/login", { method: "POST", body: JSON.stringify({ username: $("#login-username").value.trim(), password: $("#login-password").value }) });
      state.csrf = result.csrf_token;
      showApp(); await loadDashboard(); toast("Welcome back to Taste Safari Admin.");
    } catch (error) { $("#login-error").textContent = error.message; }
    finally { button.disabled = false; button.textContent = "Sign in securely"; }
  });

  $("#logout-btn").addEventListener("click", async () => { await api("/api/admin/logout", { method: "POST" }); state.csrf = ""; location.reload(); });
  $("#menu-toggle").addEventListener("click", () => $(".sidebar").classList.toggle("open"));

  $$(".nav-item").forEach(button => button.addEventListener("click", () => {
    $$(".nav-item").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    $$(".view").forEach(view => view.classList.remove("active"));
    $(`#view-${button.dataset.view}`).classList.add("active");
    $("#page-title").textContent = button.textContent.trim();
    $(".sidebar").classList.remove("open");
    const loaders = { dashboard: loadDashboard, adventures: loadAdventures, categories: loadCategories, videos: loadVideos, gallery: loadGallery, collaborations: loadCollaborations };
    (loaders[button.dataset.view] || (() => {}))();
  }));

  $$("[data-goto]").forEach(button => button.addEventListener("click", () => $(`.nav-item[data-view="${button.dataset.goto}"]`).click()));

  async function loadDashboard() {
    const [stats, recent] = await Promise.all([api("/api/admin/stats"), api("/api/admin/recent-activity")]);
    const cards = [
      ["Food adventures", stats.adventures, `${stats.published_adventures} published`],
      ["Videos", stats.videos, `${stats.published_videos} published`],
      ["Gallery photographs", stats.gallery, `${stats.published_gallery} published`],
      ["Collaboration enquiries", stats.collaborations, `${stats.new_collaborations} new`],
      ["Categories", stats.categories, "shared filters"],
    ];
    $("#stats-grid").innerHTML = cards.map(([label, value, note]) => `<div class="stat-card"><span>${label}</span><strong>${value}</strong><span class="muted">${note}</span></div>`).join("");
    renderActivity("recent-adventures", recent.adventures, item => `<strong>${esc(item.place_name)}</strong><span>${esc(item.location)} · ★ ${esc(item.rating)} · ${esc(item.status)}</span>`);
    renderActivity("recent-videos", recent.videos, item => `<strong>${esc(item.title)}</strong><span>${esc(item.video_type.replace("_", " "))} · ${esc(item.status)}</span>`);
    renderActivity("recent-gallery", recent.gallery, item => `<strong>${esc(item.title)}</strong><span>${esc(item.status)} · ${fmtDate(item.created_at)}</span>`);
    renderActivity("recent-collaborations", recent.collaborations, item => `<strong>${esc(item.name)}</strong><span>${esc(item.collaboration_type || "General")} · ${esc(item.status)}</span>`);
  }

  function renderActivity(id, items, template) {
    const target = $(`#${id}`);
    target.innerHTML = items.length ? items.map(item => `<div class="activity-item">${template(item)}</div>`).join("") : `<div class="empty">No records yet.</div>`;
  }

  async function loadCategories() {
    const data = await api(`/api/admin/categories?per_page=100&search=${encodeURIComponent($("#category-search").value)}`);
    $("#categories-table").innerHTML = `<table><thead><tr><th>Name</th><th>Slug</th><th>Adventures</th><th>Videos</th><th>Actions</th></tr></thead><tbody>${data.items.map(item => `<tr><td><strong>${esc(item.name)}</strong></td><td>${esc(item.slug)}</td><td>${item.adventure_count}</td><td>${item.video_count}</td><td><div class="row-actions"><button class="mini-btn amber" onclick="editCategory(${item.id})">Rename</button><button class="mini-btn danger" onclick="deleteCategory(${item.id},${item.adventure_count + item.video_count})">Delete</button></div></td></tr>`).join("") || `<tr><td colspan="5" class="empty">No categories found.</td></tr>`}</tbody></table>`;
    state.categories = data.items;
    fillCategorySelects();
  }

  window.editCategory = async id => {
    const item = state.categories.find(category => category.id === id);
    if (!item) return;
    modal(`<div class="modal-head"><h3>Rename category</h3><button class="close-btn">×</button></div><form id="category-form" class="form-grid"><label>Category name<input name="name" value="${esc(item.name)}" required></label><button class="primary-btn">Save category</button></form>`);
    $("#category-form").onsubmit = async event => { event.preventDefault(); const form = new FormData(event.target); try { await api(`/api/admin/categories/${id}`, { method: "PUT", body: JSON.stringify({ name: form.get("name") }) }); closeModal(); toast("Category updated."); loadCategories(); loadAdventures(); } catch (error) { toast(error.message, "error"); } };
  };

  window.deleteCategory = async (id, count) => {
    if (!confirm(count ? `This category is used by ${count} records. Deletion is blocked until they are reassigned.` : "Delete this category?")) return;
    if (count) return toast("Reassign or remove linked content first.", "error");
    try { await api(`/api/admin/categories/${id}`, { method: "DELETE" }); toast("Category deleted."); loadCategories(); } catch (error) { toast(error.message, "error"); }
  };

  $("#add-category").addEventListener("click", () => {
    modal(`<div class="modal-head"><h3>New category</h3><button class="close-btn">×</button></div><form id="category-form" class="form-grid"><label>Category name<input name="name" required placeholder="e.g. Cloud Kitchen"></label><button class="primary-btn">Create category</button></form>`);
    $("#category-form").onsubmit = async event => { event.preventDefault(); const form = new FormData(event.target); try { await api("/api/admin/categories", { method: "POST", body: JSON.stringify({ name: form.get("name") }) }); closeModal(); toast("Category created."); loadCategories(); } catch (error) { toast(error.message, "error"); } };
  });

  $("#category-search").addEventListener("input", debounce(loadCategories, 250));

  async function loadAdventures() {
    const params = new URLSearchParams({ page: state.page, per_page: 10, search: $("#adventure-search").value, category_id: $("#adventure-category-filter").value, status: $("#adventure-status-filter").value, sort: $("#adventure-sort").value });
    const data = await api(`/api/admin/adventures?${params}`);
    $("#adventures-table").innerHTML = `<table><thead><tr><th>Photo</th><th>Place</th><th>Category</th><th>Rating</th><th>Status</th><th>Actions</th></tr></thead><tbody>${data.items.map(item => `<tr><td>${item.photo_url ? `<img class="thumb" src="${item.photo_url}">` : '<div class="thumb"></div>'}</td><td><strong>${esc(item.place_name)}</strong><br><span class="muted">${esc(item.location)}</span></td><td>${esc(item.category_name)}</td><td>★ ${Number(item.rating).toFixed(1)}</td><td><span class="pill ${item.status === 'draft' ? 'draft' : ''}">${esc(item.status)}</span></td><td><div class="row-actions"><button class="mini-btn" onclick="editAdventure(${item.id})">Edit</button><button class="mini-btn amber" onclick="toggleAdventure(${item.id},'${item.status === 'published' ? 'draft' : 'published'}')">${item.status === 'published' ? 'Unpublish' : 'Publish'}</button><button class="mini-btn danger" onclick="deleteAdventure(${item.id})">Delete</button></div></td></tr>`).join("") || `<tr><td colspan="6" class="empty">No adventures found.</td></tr>`}</tbody></table>`;
    renderPagination("#adventures-pagination", data.total, data.per_page, state.page, page => { state.page = page; loadAdventures(); });
  }

  function adventureForm(item = {}) {
    const categories = state.categories.length ? state.categories : [];
    return `<form id="adventure-form" class="form-grid"><div class="input-grid"><label>Location<input name="location" required value="${esc(item.location || '')}"></label><label>Name of Place<input name="place_name" required value="${esc(item.place_name || '')}"></label><label>Rating<input name="rating" type="number" min="0" max="10" step="0.1" required value="${esc(item.rating ?? '')}"></label><label>Price Range<input name="price_range" value="${esc(item.price_range || '')}" placeholder="₹100–₹250"></label><label>Timing<input name="timing" value="${esc(item.timing || '')}" placeholder="10 AM – 10 PM"></label><label>Dietary Type<select name="dietary_type">${["Vegetarian","Vegan","Eggetarian","Non-Vegetarian"].map(v => `<option ${item.dietary_type === v ? 'selected' : ''}>${v}</option>`).join("")}</select></label><label>Category<select name="category_id" required>${categories.map(c => `<option value="${c.id}" ${item.category_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join("")}</select></label><label>Reel Link<input name="reel_link" type="url" value="${esc(item.reel_link || '')}" placeholder="https://instagram.com/.../reel/"></label><label>Status<select name="status"><option value="published" ${item.status !== 'draft' ? 'selected' : ''}>Published</option><option value="draft" ${item.status === 'draft' ? 'selected' : ''}>Draft</option></select></label></div><label>About Dish<textarea name="about_dish" required rows="4">${esc(item.about_dish || '')}</textarea></label><div class="two-col"><label>Notes<textarea name="notes" rows="4">${esc(item.notes || '')}</textarea></label><label>Recommendation<textarea name="recommendation" rows="4">${esc(item.recommendation || '')}</textarea></label></div><label>Food Photograph<input name="photo" type="file" accept="image/*"><div class="progress"><span id="photo-progress"></span></div>${item.photo_url ? `<img class="upload-preview" src="${item.photo_url}">` : ''}</label><button class="primary-btn">${item.id ? 'Save adventure' : 'Create adventure'}</button></form>`;
  }

  function openAdventureModal(item = null) {
    modal(`<div class="modal-head"><h3>${item ? 'Edit adventure' : 'New adventure'}</h3><button class="close-btn">×</button></div>${adventureForm(item || {})}`);
    $("#adventure-form").onsubmit = async event => {
      event.preventDefault();
      const button = $("button.primary-btn", $("#adventure-form"));
      button.disabled = true; button.textContent = "Saving...";
      const form = new FormData(event.target);
      try {
        const path = item ? `/api/admin/adventures/${item.id}` : "/api/admin/adventures";
        await api(path, { method: item ? "PUT" : "POST", body: form });
        closeModal(); toast(item ? "Adventure updated." : "Adventure created."); loadAdventures(); loadDashboard();
      } catch (error) { toast(error.message, "error"); button.disabled = false; button.textContent = item ? "Save adventure" : "Create adventure"; }
    };
  }

  window.editAdventure = async id => {
    const data = await api(`/api/admin/adventures/${id}`);
    if (!state.categories.length) await loadCategories();
    openAdventureModal(data);
  };
  window.toggleAdventure = async (id, status) => { try { await api(`/api/admin/adventures/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }); toast("Status updated."); loadAdventures(); } catch (error) { toast(error.message, "error"); } };
  window.deleteAdventure = async id => { if (!confirm("Delete this adventure and its uploaded photo?")) return; try { await api(`/api/admin/adventures/${id}`, { method: "DELETE" }); toast("Adventure deleted."); loadAdventures(); } catch (error) { toast(error.message, "error"); } };
  $("#add-adventure").addEventListener("click", async () => { if (!state.categories.length) await loadCategories(); openAdventureModal(); });
  ["#adventure-search", "#adventure-category-filter", "#adventure-status-filter", "#adventure-sort"].forEach(sel => $(sel).addEventListener("input", debounce(() => { state.page = 1; loadAdventures(); }, 250)));

  async function loadVideos() {
    const params = new URLSearchParams({ page: state.videoPage, per_page: 9, search: $("#video-search").value, video_type: $("#video-type-filter").value, category_id: $("#video-category-filter").value, status: $("#video-status-filter").value });
    const data = await api(`/api/admin/videos?${params}`);
    $("#videos-grid").innerHTML = data.items.map(item => `<article class="video-card"><div class="video-media">${item.thumbnail_url ? `<img src="${item.thumbnail_url}">` : (item.video_url ? `<video src="${item.video_url}" muted></video>` : '<div class="empty">No preview</div>')}</div><div class="video-body"><span class="badge">${esc(item.kind.replace("_", " "))}</span><h4>${esc(item.title)}</h4><p>${esc(item.category_name)} · ${esc(item.platform)}</p><div class="row-actions"><button class="mini-btn" onclick="editVideo(${item.id})">Edit</button><button class="mini-btn amber" onclick="toggleVideo(${item.id},'${item.status === 'published' ? 'draft' : 'published'}')">${item.status === 'published' ? 'Unpublish' : 'Publish'}</button><button class="mini-btn danger" onclick="deleteVideo(${item.id})">Delete</button></div></div></article>`).join("") || `<div class="empty">No videos found.</div>`;
    renderPagination("#videos-pagination", data.total, data.per_page, state.videoPage, page => { state.videoPage = page; loadVideos(); });
  }

  function videoForm(item = {}) {
    return `<form id="video-form" class="form-grid"><div class="input-grid"><label>Video Type<select name="video_type"><option value="full_video" ${item.video_type === 'full_video' ? 'selected' : ''}>Full Video</option><option value="external_reel" ${item.video_type === 'external_reel' ? 'selected' : ''}>External Reel</option></select></label><label>Video Title<input name="title" required value="${esc(item.title || '')}"></label><label>Instagram Reel Link<input name="reel_link" type="url" required value="${esc(item.reel_link || '')}" placeholder="https://instagram.com/.../reel/"></label><label>Category<select name="category_id" required>${state.categories.map(c => `<option value="${c.id}" ${item.category_id === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join("")}</select></label><label>Status<select name="status"><option value="published" ${item.status !== 'draft' ? 'selected' : ''}>Published</option><option value="draft" ${item.status === 'draft' ? 'selected' : ''}>Draft</option></select></label></div><label>Description<textarea name="description" rows="4">${esc(item.description || '')}</textarea></label><div class="two-col"><label>Video file<input name="video" type="file" accept="video/mp4,video/webm,video/quicktime,.mov"><div class="progress"><span id="video-progress"></span></div></label><label>Thumbnail<input name="thumbnail" type="file" accept="image/*"><div class="progress"><span id="thumb-progress"></span></div>${item.thumbnail_url ? `<img class="upload-preview" src="${item.thumbnail_url}">` : ''}</label></div><p class="muted">Full videos require an Instagram reel link and an MP4/WebM file. Large videos upload directly to Cloudinary when configured. External reels use the link for public playback.</p><button class="primary-btn">${item.id ? 'Save video' : 'Create video'}</button></form>`;
  }

  function openVideoModal(item = null) {
    modal(`<div class="modal-head"><h3>${item ? 'Edit video' : 'New video'}</h3><button class="close-btn">×</button></div>${videoForm(item || {})}`);
    $("#video-form").onsubmit = async event => {
      event.preventDefault();
      const form = new FormData(event.target);
      const button = $("button.primary-btn", $("#video-form"));
      button.disabled = true; button.textContent = "Saving...";
      try {
        const videoFile = form.get("video");
        if (videoFile instanceof File && videoFile.size && form.get("video_type") === "full_video") {
          const progress = $("#video-progress");
          if (progress) progress.textContent = "Uploading video to Cloudinary...";
          const publicId = await cloudinaryUpload(videoFile, "video", "videos");
          if (publicId) {
            form.delete("video");
            form.append("video_public_id", publicId);
            if (progress) progress.textContent = "Video uploaded.";
          }
        }
        const path = item ? `/api/admin/videos/${item.id}` : "/api/admin/videos";
        await api(path, { method: item ? "PUT" : "POST", body: form });
        closeModal(); toast(item ? "Video updated." : "Video created."); loadVideos(); loadDashboard();
      } catch (error) { toast(error.message, "error"); button.disabled = false; button.textContent = item ? "Save video" : "Create video"; }
    };
  }
  window.editVideo = async id => { const item = await api(`/api/admin/videos/${id}`); if (!state.categories.length) await loadCategories(); openVideoModal(item); };
  window.toggleVideo = async (id, status) => { try { await api(`/api/admin/videos/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }); toast("Status updated."); loadVideos(); } catch (error) { toast(error.message, "error"); } };
  window.deleteVideo = async id => { if (!confirm("Delete this video and its files?")) return; try { await api(`/api/admin/videos/${id}`, { method: "DELETE" }); toast("Video deleted."); loadVideos(); } catch (error) { toast(error.message, "error"); } };
  $("#add-video").addEventListener("click", async () => { if (!state.categories.length) await loadCategories(); openVideoModal(); });
  ["#video-search", "#video-type-filter", "#video-category-filter", "#video-status-filter"].forEach(sel => $(sel).addEventListener("input", debounce(() => { state.videoPage = 1; loadVideos(); }, 250)));

  async function loadGallery() {
    const params = new URLSearchParams({ page: state.galleryPage, per_page: 12, search: $("#gallery-search").value, status: $("#gallery-status-filter").value });
    const data = await api(`/api/admin/gallery?${params}`);
    $("#gallery-grid").innerHTML = data.items.map(item => `<article class="gallery-card"><img src="${item.image_url}"><div class="gallery-card-body"><span class="pill ${item.status === 'draft' ? 'draft' : ''}">${esc(item.status)}</span><h4>${esc(item.title)}</h4><p>${esc(item.description || 'No description')}</p><div class="row-actions"><button class="mini-btn" onclick="editGallery(${item.id})">Edit</button><button class="mini-btn amber" onclick="toggleGallery(${item.id},'${item.status === 'published' ? 'draft' : 'published'}')">${item.status === 'published' ? 'Unpublish' : 'Publish'}</button><button class="mini-btn danger" onclick="deleteGallery(${item.id})">Delete</button></div></div></article>`).join("") || `<div class="empty">No photographs found.</div>`;
    renderPagination("#gallery-pagination", data.total, data.per_page, state.galleryPage, page => { state.galleryPage = page; loadGallery(); });
  }

  function openGalleryModal(item = null) {
    modal(`<div class="modal-head"><h3>${item ? 'Edit photograph' : 'Upload photographs'}</h3><button class="close-btn">×</button></div><form id="gallery-form" class="form-grid"><div class="input-grid"><label>Title<input name="title" value="${esc(item?.title || '')}" ${item ? 'required' : ''}></label><label>Display order<input name="display_order" type="number" value="${item?.display_order ?? 0}"></label><label>Status<select name="status"><option value="published" ${item?.status !== 'draft' ? 'selected' : ''}>Published</option><option value="draft" ${item?.status === 'draft' ? 'selected' : ''}>Draft</option></select></label></div><label>Description<textarea name="description" rows="4">${esc(item?.description || '')}</textarea></label>${item ? '' : '<label>Images<input name="images" type="file" accept="image/*" multiple required></label><div id="gallery-file-fields"></div>'}${item ? '<label>Replace image<input name="image" type="file" accept="image/*"></label>' : ''}<button class="primary-btn">${item ? 'Save photograph' : 'Upload photographs'}</button></form>`);
    if (!item) {
      const input = $("#gallery-form input[name='images']");
      const container = $("#gallery-file-fields");
      input.addEventListener("change", () => {
        const files = [...input.files];
        container.innerHTML = files.map((file, index) => `<div class="two-col" style="margin-top:10px"><label>${esc(file.name)} title<input name="titles" value="${esc(file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' '))}"></label><label>Description<textarea name="descriptions" rows="2"></textarea></label></div>`).join("");
      });
    }
    $("#gallery-form").onsubmit = async event => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        if (item) await api(`/api/admin/gallery/${item.id}`, { method: "PUT", body: form });
        else await api("/api/admin/gallery", { method: "POST", body: form });
        closeModal(); toast(item ? "Photograph updated." : "Photographs uploaded."); loadGallery(); loadDashboard();
      } catch (error) { toast(error.message, "error"); }
    };
  }
  window.editGallery = async id => { const item = await api(`/api/admin/gallery/${id}`); openGalleryModal(item); };
  window.toggleGallery = async (id, status) => { try { await api(`/api/admin/gallery/${id}`, { method: "PUT", body: JSON.stringify({ status }) }); toast("Status updated."); loadGallery(); } catch (error) { toast(error.message, "error"); } };
  window.deleteGallery = async id => { if (!confirm("Delete this photograph?")) return; try { await api(`/api/admin/gallery/${id}`, { method: "DELETE" }); toast("Photograph deleted."); loadGallery(); } catch (error) { toast(error.message, "error"); } };
  $("#add-gallery").addEventListener("click", () => openGalleryModal());
  ["#gallery-search", "#gallery-status-filter"].forEach(sel => $(sel).addEventListener("input", debounce(() => { state.galleryPage = 1; loadGallery(); }, 250)));

  async function loadCollaborations() {
    const params = new URLSearchParams({ page: state.collabPage, per_page: 10, search: $("#collab-search").value, status: $("#collab-status-filter").value });
    const data = await api(`/api/admin/collaborations?${params}`);
    $("#collaborations-table").innerHTML = `<table><thead><tr><th>Sender</th><th>Type</th><th>Message</th><th>Status</th><th>Submitted</th><th>Actions</th></tr></thead><tbody>${data.items.map(item => `<tr class="${item.status === 'new' ? 'new-row' : ''}"><td><strong>${esc(item.name)}</strong><br><a href="mailto:${esc(item.email)}" class="muted">${esc(item.email)}</a></td><td>${esc(item.collaboration_type || 'General')}</td><td>${esc(item.message.slice(0, 90))}${item.message.length > 90 ? '…' : ''}</td><td><span class="pill ${item.status === 'new' ? '' : 'draft'}">${esc(item.status.replace('_', ' '))}</span></td><td>${fmtDate(item.submitted_at)}</td><td><div class="row-actions"><button class="mini-btn" onclick="openCollab(${item.id})">Open</button><button class="mini-btn amber" onclick="setCollabStatus(${item.id},'closed')">Close</button><button class="mini-btn danger" onclick="deleteCollab(${item.id})">Delete</button></div></td></tr>`).join("") || `<tr><td colspan="6" class="empty">No enquiries found.</td></tr>`}</tbody></table>`;
    renderPagination("#collaborations-pagination", data.total, data.per_page, state.collabPage, page => { state.collabPage = page; loadCollaborations(); });
  }

  window.openCollab = async id => {
    const item = await api(`/api/admin/collaborations/${id}`);
    modal(`<div class="modal-head"><h3>${esc(item.name)}</h3><button class="close-btn">×</button></div><div class="form-grid"><div class="two-col"><div><strong>Email</strong><br><a href="mailto:${esc(item.email)}">${esc(item.email)}</a></div><div><strong>Phone</strong><br>${esc(item.phone || '—')}</div><div><strong>Brand</strong><br>${esc(item.brand || '—')}</div><div><strong>Type</strong><br>${esc(item.collaboration_type || 'General')}</div></div><label>Status<select id="collab-status">${["new","read","in_progress","replied","closed"].map(s => `<option value="${s}" ${item.status === s ? 'selected' : ''}>${s.replace('_',' ')}</option>`).join("")}</select></label><label>Message<textarea rows="8" readonly>${esc(item.message)}</textarea></label><button class="primary-btn" id="save-collab-status">Update status</button></div>`);
    $("#save-collab-status").onclick = async () => { try { await api(`/api/admin/collaborations/${id}`, { method: "PUT", body: JSON.stringify({ status: $("#collab-status").value }) }); closeModal(); toast("Status updated."); loadCollaborations(); loadDashboard(); } catch (error) { toast(error.message, "error"); } };
  };
  window.setCollabStatus = async (id, status) => { try { await api(`/api/admin/collaborations/${id}`, { method: "PUT", body: JSON.stringify({ status }) }); toast("Status updated."); loadCollaborations(); } catch (error) { toast(error.message, "error"); } };
  window.deleteCollab = async id => { if (!confirm("Delete this enquiry?")) return; try { await api(`/api/admin/collaborations/${id}`, { method: "DELETE" }); toast("Enquiry deleted."); loadCollaborations(); } catch (error) { toast(error.message, "error"); } };
  ["#collab-search", "#collab-status-filter"].forEach(sel => $(sel).addEventListener("input", debounce(() => { state.collabPage = 1; loadCollaborations(); }, 250)));

  function renderPagination(selector, total, perPage, page, onPage) {
    const pages = Math.max(1, Math.ceil(total / perPage));
    $(selector).innerHTML = pages > 1 ? Array.from({ length: pages }, (_, i) => i + 1).map(p => `<button class="${p === page ? 'primary-btn' : ''}" data-page="${p}">${p}</button>`).join("") : "";
    $$("button", $(selector)).forEach(button => button.addEventListener("click", () => onPage(Number(button.dataset.page))));
  }

  function fillCategorySelects() {
    const html = state.categories.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
    ["#adventure-category-filter", "#video-category-filter"].forEach(selector => { const select = $(selector); const value = select.value; select.innerHTML = `<option value="">All categories</option>${html}`; select.value = value; });
  }

  function debounce(fn, delay) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; }

  document.addEventListener("DOMContentLoaded", boot);
})();
