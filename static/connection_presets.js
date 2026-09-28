(function () {
    // Suggestions from the user's working port list, plus the previously documented
    // Crestron and NETGEAR entries, and manufacturer-documented examples.
    // These are editable hints, not service detection.
    const entries = [
        ["crestron", "Crestron control system · SSH", "ssh", "22"],
        ["netgear", "NETGEAR managed switch · Telnet", "telnet", "23"],
        ["bose", "Bose · TCP", "tcp", "10055"],
        ["i3", "i3 · TCP", "tcp", "4664"],
        ["marshall", "Marshall VMV402SH · TCP", "tcp", "9760", "vmv402sh"],
        ["samsung", "Samsung · TCP", "tcp", "1515"],
        ["sennheiser", "Sennheiser TCC2 · TCP", "tcp", "45", "tcc2"],
        ["sennheiser", "Sennheiser TCC2 · UDP", "udp", "45", "tcc2"],
        ["sennheiser", "Sennheiser TCC2 · SSH", "ssh", "22", "tcc2"],
        ["shure", "Shure control strings · TCP", "tcp", "2202"],
        ["lumens", "Lumens camera VISCA · UDP", "udp", "52381"],
        [null, "VISCA camera · UDP", "udp", "52381"],
        ["blackmagic", "Blackmagic Videohub · TCP", "tcp", "9990", "videohub"],
        ["nec", "NEC projector/display · TCP", "tcp", "7142"],
        ["sony", "Sony Bravia · TCP", "tcp", "20060", "bravia"],
        ["sennheiser", "Sennheiser OSC · UDP", "udp", "45", "osc"],
        ["sennheiser", "Sennheiser OSC · TCP", "tcp", "45", "osc"],
        [null, "M32 OSC · UDP", "udp", "10023"],
        [null, "KNX · TCP", "tcp", "10001"],
        ["clevertouch", "Clevertouch · TCP", "tcp", "4664"],
        ["extron", "Extron · Telnet", "telnet", "23"],
        ["audac", "Audac XMP · TCP", "tcp", "5001", "xmp"],
        ["ateis", "Ateis UAP G2 · UDP", "udp", "19761", "uap"],
        ["global cache", "Global Caché iTach · TCP", "tcp", "4999", "itach"],
        ["epson", "Epson projector · TCP", "tcp", "3629"],
        ["kramer", "Kramer VP558 · UDP", "udp", "50000", "vp558"],
        ["kramer", "Kramer VP558 · TCP", "tcp", "5000", "vp558"],
        ["panasonic", "Panasonic camera · TCP", "tcp", "80"],
        ["biamp", "Tesira Forte · Telnet", "telnet", "23", "tesira"],
        ["biamp", "Tesira · SSH", "ssh", "22", "tesira", "https://support.biamp.com/Tesira/Control/Tesira_network_ports_and_protocols"],
        ["qsc", "Q-SYS Core ECP · TCP", "tcp", "1702", "core", "https://help.qsys.com/q-sys_10.2/Content/Networking/Clocking_Audio_Video_Control.htm"],
        ["qsc", "Q-SYS Core QRC · TCP", "tcp", "1710", "core", "https://help.qsys.com/q-sys_10.2/Content/Networking/Clocking_Audio_Video_Control.htm"],
        ["ptzoptics", "PTZOptics VISCA · TCP", "tcp", "5678", "", "https://docs.ptzoptics.com/docs/controllers/superjoy/connections/"],
        ["ptzoptics", "PTZOptics VISCA · UDP", "udp", "1259", "", "https://docs.ptzoptics.com/docs/controllers/superjoy/connections/"],
        ["lightware", "Lightware HDMI matrix · TCP", "tcp", "6107"],
        ["blustream", "Blustream matrix · TCP", "tcp", "8000"],
        [null, "AH-ERT-30 · TCP", "tcp", "2002"],
        ["sony", "Sony projector · TCP", "tcp", "53595", "projector"],
        ["optoma", "Optoma projector · TCP", "tcp", "2023"],
        ["inogeni", "Inogeni · TCP", "tcp", "50000"],
        ["roland", "Roland XS-62S · TCP", "tcp", "8023", "xs-62s"],
        ["1beyond", "1Beyond VISCA · TCP", "tcp", "5500"],
        ["skaarhoj", "Skaarhoj · Telnet", "telnet", "8899"],
        ["tascam", "Tascam SS-R250N · Telnet", "telnet", "23", "ss-r250n"],
        ["sony", "Sony camera VISCA · UDP", "udp", "52381", "camera"]
    ];

    const catalog = entries.map(([vendor, label, protocol, port, model, source], index) => ({
        id: String(index), vendor, label, protocol, port, model: model || "",
        note: source
            ? "From manufacturer documentation. Check the device model and whether the service is enabled."
            : index < 2
            ? "Common for some devices from this manufacturer. Check model support and whether the service is enabled."
            : "From the working port list. Check the device model and settings before connecting.",
        source: source || null
    }));

    function normalized(value) {
        return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase();
    }

    function suggestions(manufacturer, hostname = "") {
        const name = normalized(manufacturer);
        if (!name || ["unknown", "looking up...", "-", "this pc"].includes(name)) return [];
        const host = normalized(hostname);
        const nameKey = name.replace(/[^a-z0-9]/g, "");
        return catalog.filter(item => item.vendor
                && nameKey.startsWith(normalized(item.vendor).replace(/[^a-z0-9]/g, "")))
            .sort((a, b) => Number(Boolean(b.model && host.includes(b.model)))
                - Number(Boolean(a.model && host.includes(a.model))));
    }

    function suggest(manufacturer, hostname = "") {
        return suggestions(manufacturer, hostname)[0] || null;
    }

    window.AVConnectionPresets = {suggest, suggestions, all: () => [...catalog]};
})();
