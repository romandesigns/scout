const VERSION="6.7.7";
const SHELL=`scout-shell-${VERSION}`;
const APP_SHELL=["/","/manifest.webmanifest","/icons/scout-192.png","/icons/scout-512.png"];

self.addEventListener("install",event=>event.waitUntil(caches.open(SHELL).then(cache=>cache.addAll(APP_SHELL)).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith("scout-shell-")&&key!==SHELL).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",event=>{
  const request=event.request;
  if(request.method!=="GET")return;
  const url=new URL(request.url);
  if(url.pathname.startsWith("/api/")||url.pathname.startsWith("/charts/")){
    event.respondWith(fetch(request));
    return;
  }
  event.respondWith(fetch(request).then(response=>{const copy=response.clone();caches.open(SHELL).then(cache=>cache.put(request,copy));return response;}).catch(()=>caches.match(request).then(match=>match||caches.match("/"))));
});
self.addEventListener("push",event=>{
  let payload={};
  try{payload=event.data?.json()||{};}catch{payload={body:event.data?.text()||"New Scout opportunity"};}
  event.waitUntil(self.registration.showNotification(payload.title||"Scout opportunity",{body:payload.body||"Open Scout to review.",icon:"/icons/scout-192.png",badge:"/icons/scout-192.png",tag:payload.tag||payload.ticker||"scout",renotify:payload.renotify!==false,requireInteraction:Boolean(payload.requireInteraction),vibrate:payload.vibrate||[120],data:{url:payload.url||"/?view=alerts",findingId:payload.findingId,ticker:payload.ticker,stage:payload.stage},actions:[{action:"open",title:"Open event"},{action:"dismiss",title:"Dismiss"}]}));
});
self.addEventListener("notificationclick",event=>{
  event.notification.close();
  if(event.action==="dismiss")return;
  const target=event.notification.data?.url||"/?view=alerts";
  event.waitUntil(clients.matchAll({type:"window",includeUncontrolled:true}).then(windows=>{const existing=windows.find(client=>"focus" in client);if(existing){existing.navigate(target);return existing.focus();}return clients.openWindow(target);}));
});
self.addEventListener("message",event=>{if(event.data?.type==="SKIP_WAITING")self.skipWaiting();});
