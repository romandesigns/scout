"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type InstallPrompt=Event&{prompt:()=>Promise<void>;userChoice:Promise<{outcome:string}>};

export function PwaRuntime(){
  const [install,setInstall]=useState<InstallPrompt|null>(null);
  const [update,setUpdate]=useState<ServiceWorker|null>(null);
  const [online,setOnline]=useState(true);
  useEffect(()=>{
    const nativeShell="__TAURI_INTERNALS__" in window||"__TAURI__" in window||window.location.hostname==="tauri.localhost";
    const clientVersion=process.env.NEXT_PUBLIC_SCOUT_VERSION||"dev";
    setOnline(navigator.onLine);
    const onOnline=()=>setOnline(true),onOffline=()=>setOnline(false);
    const onInstall=(event:Event)=>{event.preventDefault();setInstall(event as InstallPrompt);};
    window.addEventListener("online",onOnline);window.addEventListener("offline",onOffline);window.addEventListener("beforeinstallprompt",onInstall);
    if(nativeShell&&"serviceWorker" in navigator){
      // Native releases ship their assets with the executable. Remove any
      // service worker left by pre-6.1 builds so it cannot mix release files.
      const migrationKey=`stockhunter-native-assets-${clientVersion}`;
      if(localStorage.getItem(migrationKey)!=="ready"){
        void Promise.all([navigator.serviceWorker.getRegistrations(),"caches" in window?caches.keys():Promise.resolve([] as string[])]).then(async([workers,keys])=>{
          await Promise.all([...workers.map(item=>item.unregister()),...keys.map(key=>caches.delete(key))]);
          localStorage.setItem(migrationKey,"ready");
          if(workers.length||keys.length)window.location.reload();
        });
      }
    }else if("serviceWorker" in navigator){void navigator.serviceWorker.register("/sw.js").then(registration=>{
      if(registration.waiting)setUpdate(registration.waiting);
      registration.addEventListener("updatefound",()=>registration.installing?.addEventListener("statechange",()=>{if(registration.waiting)setUpdate(registration.waiting);}));
    });}
    return()=>{window.removeEventListener("online",onOnline);window.removeEventListener("offline",onOffline);window.removeEventListener("beforeinstallprompt",onInstall);};
  },[]);
  if(online&&!install&&!update)return null;
  return <div className="pwa-runtime" role="status">
    {!online&&<span>Offline shell · live market data paused</span>}
    {install&&<Button className="h-6 px-1.5 text-[10px]" onClick={()=>{void install.prompt();void install.userChoice.finally(()=>setInstall(null));}}>Install Scout</Button>}
    {update&&<Button className="h-6 px-1.5 text-[10px]" onClick={()=>{navigator.serviceWorker.addEventListener("controllerchange",()=>window.location.reload(),{once:true});update.postMessage({type:"SKIP_WAITING"});}}>Update available</Button>}
  </div>;
}
