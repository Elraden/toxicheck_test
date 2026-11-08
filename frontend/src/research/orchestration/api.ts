import { request } from '../../services/http';
import { browserSession, csrfHeaders } from '../../services/userSession';
export const root='/admin/orchestration';
export function read<T>(path:string) { return request<T>(root+path,{cache:'no-store'}); }
export function write<T>(path:string,value:unknown,method='POST') {
  return request<T>(root+path,{method,headers:csrfHeaders(browserSession.value?.admin_csrf),body:JSON.stringify(value)});
}
export function download(value:unknown,name:string){
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
