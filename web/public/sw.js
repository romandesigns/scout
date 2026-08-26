const VERSION="6.11.2";
const SHELL=`scout-shell-${VERSION}-v2`;
const APP_SHELL=["/","/manifest.webmanifest","/icons/scout-192.png","/icons/scout-512.png"];

self.addEventListener("install",event=>event.waitUntil(caches.open(SHELL).then(cache=>cache.addAll(APP_SHELL)).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith("scout-shell-")&&key!==SHELL).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",event=>{
  const request=event.request;
  if(request.method!=="GET")return;
  const url=new URL(request.url);
  if(url.origin!==self.location.origin)return;
  if(url.pathname.startsWith("/api/")||url.pathname.startsWith("/charts/")){
    event.respondWith(fetch(request));
    return;
  }
  event.respondWith(fetch(request).then(response=>{
    if(response.ok){const copy=response.clone();event.waitUntil(caches.open(SHELL).then(cache=>cache.put(request,copy)));}
    return response;
  }).catch(async()=>{
    const match=await caches.match(request);
    if(match)return match;
    if(request.mode==="navigate")return caches.match("/");
    return Response.error();
  }));
});
self.addEventListener("push",event=>{
  let payload={};
  try{payload=event.data?.json()||{};}catch{payload={body:event.data?.text()||"New Scout opportunity"};}
  event.waitUntil(clients.matchAll({type:"window",includeUncontrolled:true}).then(windows=>{
    if(windows.some(client=>client.visibilityState==="visible"))return;
    return self.registration.showNotification(payload.title||"Scout opportunity",{body:payload.body||"Open Scout to review.",icon:"/icons/scout-192.png",badge:"/icons/scout-192.png",tag:payload.tag||payload.ticker||"scout",renotify:payload.renotify!==false,requireInteraction:Boolean(payload.requireInteraction),vibrate:payload.vibrate||[120],data:{url:payload.url||"/?view=alerts",findingId:payload.findingId,ticker:payload.ticker,stage:payload.stage},actions:[{action:"open",title:"Open event"},{action:"dismiss",title:"Dismiss"}]}).then(()=>payload.findingId?fetch(`/api/findings/${payload.findingId}/client-displayed`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({channel:"webpush",surface:"service-worker"})}).catch(()=>{}):undefined);
  }));
});
self.addEventListener("notificationclick",event=>{
  event.notification.close();
  if(event.action==="dismiss")return;
  const target=event.notification.data?.url||"/?view=alerts";
  event.waitUntil(clients.matchAll({type:"window",includeUncontrolled:true}).then(windows=>{const existing=windows.find(client=>"focus" in client);if(existing){existing.navigate(target);return existing.focus();}return clients.openWindow(target);}));
});
self.addEventListener("message",event=>{if(event.data?.type==="SKIP_WAITING")self.skipWaiting();});
