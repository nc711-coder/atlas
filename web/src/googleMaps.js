// Loads the Google Maps JavaScript API once (no extra library needed).
let loading;

export function loadGoogleMaps() {
  if (window.google?.maps?.importLibrary) return Promise.resolve(window.google);
  if (loading) return loading;
  const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
  if (!key) return Promise.reject(new Error("VITE_GOOGLE_MAPS_API_KEY is missing (see web/.env.example)"));
  loading = new Promise((resolve, reject) => {
    window.__atlasMapsReady = () => resolve(window.google);
    const s = document.createElement("script");
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=__atlasMapsReady`;
    s.async = true;
    s.onerror = () => reject(new Error("Google Maps failed to load (check the key and its website restrictions)"));
    document.head.appendChild(s);
  });
  return loading;
}
