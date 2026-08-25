self.addEventListener('install', function(e) {
  // Service worker instalado
});

self.addEventListener('fetch', function(e) {
  // Passa as requisições direto (sem cache pesado por enquanto)
  e.respondWith(fetch(e.request));
});