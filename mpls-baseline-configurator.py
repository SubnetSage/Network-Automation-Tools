import streamlit as st
import random
import string
import ipaddress
import json
import pandas as pd
from io import BytesIO
import zipfile
from pyvis.network import Network

# ============================================================================
# 1. CORE LOGIC & TOPOLOGY
# ============================================================================

def generate_hostname(router_type, index):
    letters = ''.join(random.choices(string.ascii_uppercase, k=2))
    digits = ''.join(random.choices(string.digits, k=2))
    return f"{router_type}-{letters}{digits}"

def allocate_loopbacks(total_routers, base_network):
    network = ipaddress.IPv4Network(base_network)
    return [str(ip) for i, ip in enumerate(network.hosts()) if i < total_routers]

def allocate_p2p_links(num_links, base_network):
    network = ipaddress.IPv4Network(base_network)
    hosts = list(network.hosts())
    return [(str(hosts[i]), str(hosts[i + 1])) for i in range(0, num_links * 2, 2)]

def create_topology(num_p, num_pe):
    connections = []
    if num_p > 1:
        for i in range(num_p):
            next_p = (i + 1) % num_p
            connections.append((i, next_p))
    for i in range(num_pe):
        pe_idx = num_p + i
        p1, p2 = i % num_p, (i + 1) % num_p
        connections.append((p1, pe_idx))
        if num_p > 1:
            connections.append((p2, pe_idx))
    return connections

# ============================================================================
# 2. VISUAL EXPORT (PYVIS) - WITH HOST BIT LABELS
# ============================================================================

def generate_topology_html(routers, connection_details):
    net = Network(height="700px", width="100%", bgcolor="#ffffff", font_color="black")
    for r in routers:
        is_pe = r['type'] == "PE"
        net.add_node(
            r['hostname'], 
            label=f"{r['hostname']}\n{r['loopback']}", 
            color="#e67e22" if is_pe else "#3498db",
            size=12 if is_pe else 18, 
            shape="dot" if is_pe else "diamond"
        )
    for conn in connection_details:
        # Check if keys exist to prevent crash during transition
        if 'IP A' in conn:
            host_a = "." + conn['IP A'].split('.')[-1]
            host_b = "." + conn['IP B'].split('.')[-1]
            label_text = f"{conn['Port A']} ({host_a}) <---> ({host_b}) {conn['Port B']}"
        else:
            label_text = f"{conn.get('Port A', '??')} <---> {conn.get('Port B', '??')}"
        
        net.add_edge(
            conn['From'], conn['To'], 
            label=label_text,
            color="#95a5a6",
            font={'size': 9, 'align': 'horizontal', 'color': '#2c3e50'}
        )
    net.set_options("""
    var options = { "physics": { "barnesHut": { "gravitationalConstant": -5000, "springLength": 450 } } }
    """)
    return net.generate_html()

# ============================================================================
# 3. FULL STACK CONFIG GENERATOR
# ============================================================================

def generate_full_stack_config(r, ifaces, p_loopbacks, pe_loopbacks):
    hostname = r['hostname']
    lb = r['loopback']
    asn = "65000"
    conf = f"! *** FULL STACK + VRF CONFIG FOR {hostname} ***\n"
    conf += f"hostname {hostname}\n!\n"
    
    if r['type'] == 'PE':
        conf += f"vrf definition CUSTOMER_A\n rd {asn}:{r['index']}\n route-target both {asn}:100\n !\n address-family ipv4\n exit-address-family\n!\n"

    conf += "mpls ip\nmpls label protocol ldp\nmpls ldp router-id Loopback0 force\n!\n"
    conf += f"interface Loopback0\n ip address {lb} 255.255.255.255\n ip ospf 1 area 0\n!\n"
    
    for i, ip, m in ifaces:
        conf += f"interface {i}\n ip address {ip} {m}\n ip ospf 1 area 0\n mpls ip\n mpls ldp interface\n no shutdown\n!\n"
    
    if r['type'] == 'PE':
        conf += f"interface GigabitEthernet99\n vrf forwarding CUSTOMER_A\n ip address 192.168.{r['index']}.1 255.255.255.0\n no shutdown\n!\n"

    conf += f"router ospf 1\n router-id {lb}\n!\n"
    conf += f"router bgp {asn}\n bgp router-id {lb}\n"
    
    peers = [p for p in (p_loopbacks + pe_loopbacks if r['type'] == 'P' else p_loopbacks) if p != lb]
    for peer in peers:
        conf += f" neighbor {peer} remote-as {asn}\n neighbor {peer} update-source Loopback0\n"
    
    conf += " address-family vpnv4\n"
    for peer in peers:
        conf += f"  neighbor {peer} activate\n  neighbor {peer} send-community both\n"
        if r['type'] == 'P': conf += f"  neighbor {peer} route-reflector-client\n"
    conf += " exit-address-family\n"
    
    if r['type'] == 'PE':
        conf += " address-family ipv4 vrf CUSTOMER_A\n  redistribute connected\n exit-address-family\n"
            
    return conf + "!\nend\n"

# ============================================================================
# 4. STREAMLIT UI
# ============================================================================

def main():
    st.set_page_config(page_title="MPLS Lab Designer", layout="wide")
    st.title("🌐 The Ultimate MPLS Lab Designer")

    with st.sidebar:
        st.header("Lab Parameters")
        num_p = st.number_input("P Nodes", 2, 10, 3)
        num_pe = st.number_input("PE Nodes", 2, 20, 4)
        lp_pool = st.text_input("Loopback Pool", "10.255.0.0/24")
        p2p_pool = st.text_input("P2P Pool", "10.0.0.0/24")
        generate_btn = st.button("🚀 Build Lab", type="primary")

    if generate_btn:
        total = num_p + num_pe
        loopbacks = allocate_loopbacks(total, lp_pool)
        topology = create_topology(num_p, num_pe)
        p2p_links = allocate_p2p_links(len(topology), p2p_pool)
        
        routers, p_lbs, pe_lbs = [], [], []
        for i in range(num_p):
            r = {'type': 'P', 'hostname': generate_hostname("P", i), 'loopback': loopbacks[i], 'index': i}
            routers.append(r); p_lbs.append(r['loopback'])
        for i in range(num_pe):
            r = {'type': 'PE', 'hostname': generate_hostname("PE", i), 'loopback': loopbacks[num_p+i], 'index': num_p+i}
            routers.append(r); pe_lbs.append(r['loopback'])

        router_interfaces = {i: [] for i in range(total)}
        conn_details = []
        iface_counters = {i: 0 for i in range(total)}

        for idx, (a, b) in enumerate(topology):
            ip_a, ip_b = p2p_links[idx]
            if_a, if_b = f"Gi0/{iface_counters[a]}", f"Gi0/{iface_counters[b]}"
            router_interfaces[a].append((if_a, ip_a, "255.255.255.254"))
            router_interfaces[b].append((if_b, ip_b, "255.255.255.254"))
            
            conn_details.append({
                'From': routers[a]['hostname'], 
                'Port A': if_a, 
                'IP A': ip_a,
                'To': routers[b]['hostname'], 
                'Port B': if_b, 
                'IP B': ip_b
            })
            iface_counters[a] += 1; iface_counters[b] += 1

        configs = {r['hostname']: generate_full_stack_config(r, router_interfaces[r['index']], p_lbs, pe_lbs) for r in routers}
        st.session_state.update({'configs': configs, 'routers': routers, 'conn_details': conn_details, 'ai_context': {"inventory": routers, "topology": conn_details, "configs": configs}})

    # CRITICAL GUARDRAIL: Check for both existence and correct structure
    if 'configs' in st.session_state and 'IP A' in st.session_state['conn_details'][0]:
        c1, c2, c3 = st.columns(3)
        with c1:
            buf = BytesIO()
            with zipfile.ZipFile(buf, 'w') as zf:
                for h, c in st.session_state['configs'].items(): zf.writestr(f"{h}.txt", c)
            st.download_button("📦 ZIP Configs", buf.getvalue(), "lab_configs.zip")
        with c2:
            html = generate_topology_html(st.session_state['routers'], st.session_state['conn_details'])
            st.download_button("🌐 Download Diagram (HTML)", html, "topology.html", "text/html")
        with c3:
            st.download_button("🤖 AI JSON Context", json.dumps(st.session_state['ai_context'], indent=4), "ai_context.json")

        st.subheader("📋 Detailed Interconnect Table")
        st.table(pd.DataFrame(st.session_state['conn_details']))
        
        st.divider()
        sel = st.selectbox("Preview Config", list(st.session_state['configs'].keys()))
        st.code(st.session_state['configs'][sel], language="bash")
    elif 'configs' in st.session_state:
        st.warning("⚠️ Session data updated. Please click '🚀 Build Lab' to refresh interface IP mapping.")

if __name__ == "__main__":
    main()
