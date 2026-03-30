const current = new URL(window.location.href);
const redirectUrl = new URL("/panel/pages/files.html", window.location.origin);

["bucket", "q"].forEach((key) => {
  const value = current.searchParams.get(key);
  if (value) {
    redirectUrl.searchParams.set(key, value);
  }
});

window.location.replace(redirectUrl.toString());
