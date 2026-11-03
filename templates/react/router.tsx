import {createContext, useContext, useEffect, useState, type ReactNode} from 'react'

type Match = {path:string; params:Record<string,string>}
type Route = {path:string; element:ReactNode}
const RouteContext = createContext<Match>({path:'/', params:{}})
const localPath = (value:string) => value.startsWith('/') && !value.startsWith('//') && !/[\\?#\u0000-\u001f]/.test(value)
const currentPath = () => {const path=window.location.hash.slice(1);return localPath(path)?path:'/'}

function match(pattern:string, path:string):Record<string,string>|null {
 const expected=pattern.split('/').filter(Boolean), actual=path.split('/').filter(Boolean)
 if(expected.length!==actual.length)return null
 const params:Record<string,string>={}
 for(let i=0;i<expected.length;i++){
  if(expected[i].startsWith(':')){try{params[expected[i].slice(1)]=decodeURIComponent(actual[i])}catch{return null}}
  else if(expected[i]!==actual[i])return null
 }
 return params
}

export function Router({routes, fallback=<main><h1>页面不存在</h1><Link to="/">返回首页</Link></main>}:{routes:Route[];fallback?:ReactNode}) {
 const [path,setPath]=useState(currentPath)
 useEffect(()=>{const update=()=>setPath(currentPath());window.addEventListener('hashchange',update);return()=>window.removeEventListener('hashchange',update)},[])
 for(const route of routes){const params=match(route.path,path);if(params!==null)return <RouteContext.Provider value={{path,params}}>{route.element}</RouteContext.Provider>}
 return <RouteContext.Provider value={{path,params:{}}}>{fallback}</RouteContext.Provider>
}
export function Link({to,children,className}:{to:string;children:ReactNode;className?:string}) {
 if(!localPath(to))throw new Error('页面链接必须是本地路径')
 return <a href={'#'+to} className={className}>{children}</a>
}
export function useRoute(){return useContext(RouteContext)}
