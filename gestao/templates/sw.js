self.addEventListener('install', function(e) {
    self.skipWaiting(); // Força a atualização do PWA imediatamente
});

self.addEventListener('fetch', function(e) {
    // Passa as requisições direto
    e.respondWith(fetch(e.request));
});