import { getPushConfig, registerPushSubscription, removePushSubscription } from "./api";

export type WebPushState={supported:boolean;configured:boolean;permission:NotificationPermission;subscribed:boolean;message:string};

function applicationKey(value:string):Uint8Array<ArrayBuffer>{
  const padded=value+"=".repeat((4-value.length%4)%4);
  const raw=atob(padded.replace(/-/g,"+").replace(/_/g,"/"));
  return Uint8Array.from(raw,char=>char.charCodeAt(0)) as Uint8Array<ArrayBuffer>;
}

export async function webPushState():Promise<WebPushState>{
  const supported=typeof window!=="undefined"&&"serviceWorker" in navigator&&"PushManager" in window&&"Notification" in window;
  if(!supported)return{supported:false,configured:false,permission:"default",subscribed:false,message:"Web Push is not supported on this device"};
  const config=await getPushConfig();
  const registration=await navigator.serviceWorker.ready;
  const subscription=await registration.pushManager.getSubscription();
  return{supported:true,configured:config.enabled,permission:Notification.permission,subscribed:Boolean(subscription),message:subscription?"Background alerts enabled":config.enabled?"Ready to enable":"Server push keys are not configured"};
}

export async function enableWebPush():Promise<WebPushState>{
  const config=await getPushConfig();
  if(!config.enabled||!config.public_key)throw new Error("Scout Web Push is not configured on the server");
  const permission=await Notification.requestPermission();
  if(permission!=="granted")throw new Error("Notification permission was not granted");
  const registration=await navigator.serviceWorker.ready;
  let subscription=await registration.pushManager.getSubscription();
  if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:applicationKey(config.public_key)});
  await registerPushSubscription(subscription.toJSON());
  return webPushState();
}

export async function disableWebPush():Promise<WebPushState>{
  const registration=await navigator.serviceWorker.ready;
  const subscription=await registration.pushManager.getSubscription();
  if(subscription){await removePushSubscription(subscription.endpoint);await subscription.unsubscribe();}
  return webPushState();
}
