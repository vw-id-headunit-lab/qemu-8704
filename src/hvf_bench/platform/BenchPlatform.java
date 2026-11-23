package bench.platform;

import de.vw.mib.osgi.MIBOsgi;
import de.vw.mib.osgi.SubRegistry;
import de.vw.mib.osgi.internal.BundleInfo;
import de.vw.mib.configuration.ConfigurationManager;
import de.vw.mib.file.FileManager;
import de.vw.mib.collections.HashCodeBuilder;
import de.vw.mib.graphics.image.spi.ImageSPI;
import org.dsi.ifc.base.DSIBase;
import org.dsi.ifc.base.DSIListener;
import org.dsi.ifc.base.ServiceAdmin;
import org.dsi.ifc.persistence.DSIPersistence;
import org.dsi.ifc.persistence.DSIPersistenceListener;
import org.dsi.ifc.generalvehiclestates.DSIGeneralVehicleStates;
import org.dsi.ifc.generalvehiclestates.DSIGeneralVehicleStatesListener;
import org.dsi.ifc.powermanagement.DSIPowerManagement;
import org.dsi.ifc.powermanagement.DSIPowerManagementListener;
import org.dsi.ifc.powermanagement.ClampSignal;
import org.dsi.ifc.keypanel.DSIKeyPanel;
import org.dsi.ifc.keypanel.DSIKeyPanelListener;
import org.dsi.ifc.keypanel.Constants;
import org.dsi.ifc.asiainput.DSIAsiaInput;
import org.dsi.ifc.asiainput.DSIAsiaInputListener;
import org.osgi.framework.BundleContext;
import org.osgi.framework.ServiceReference;
import org.osgi.framework.ServiceRegistration;

import java.lang.reflect.Array;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.io.ByteArrayInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Dictionary;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Hashtable;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.Properties;
import java.net.HttpURLConnection;
import java.net.URL;

/** External bench platform services. The shipped MIB UI and its classes are untouched. */
public final class BenchPlatform {
    private static final int INSTANCE = 0;
    private static final int IDENTIFICATION_NAMESPACE = 50331648; // 0x03000000
    private static final long MU_SOFTWARE_VERSION_KEY = 61833L; // 0xF189, four ASCII bytes
    private static final String MOCK_MU_SOFTWARE_VERSION = "MOCK";
    private static final String TAG = "[BENCH-PLATFORM] ";
    private static final List<ServiceRegistration> REGISTRATIONS = new ArrayList<ServiceRegistration>();
    private static BundleContext context;
    private static Persistence persistence;
    private static Vehicle vehicle;
    private static Power power;
    private static KeyPanel keyPanel;
    private static PointerReader pointerReader;
    private static volatile boolean active;
    private static final NativeTouch nativeTouch = new NativeTouch();

    public static void main(String[] args) throws Exception {
        if (args.length == 1 && "--self-test".equals(args[0])) {
            try { selfTest(); System.exit(0); }
            catch (Throwable t) { t.printStackTrace(); System.exit(1); }
        }
        install();
        if (System.getProperty("bench.vehicle.rsi") != null) startClimateViewCleanup();
        if (Boolean.getBoolean("bench.gateway.enabled")) startGatewayProbe();
        System.out.println(TAG + "delegating to original de.vw.mib.MIBMain");
        de.vw.mib.MIBMain.main(args);
    }

    /** The mock service leaves closed views alive and emits unchanged updates.
     * Retire only noView objects so the original client can create a fresh view.
     */
    private static void startClimateViewCleanup() {
        Thread cleanup = new Thread(new Runnable() {
            public void run() {
                while (active) {
                    try {
                        cleanupClimateViews("http://[::1]:7770");
                        Thread.sleep(250);
                    } catch (Exception error) {
                        try { Thread.sleep(1000); } catch (InterruptedException stopped) { return; }
                    }
                }
            }
        }, "BenchClimateViewCleanup");
        cleanup.setDaemon(true);
        cleanup.start();
    }

    private static void cleanupClimateViews(String base) throws IOException {
        HttpURLConnection connection = (HttpURLConnection)new URL(base + "/hvac/views/").openConnection(java.net.Proxy.NO_PROXY);
        connection.setConnectTimeout(750); connection.setReadTimeout(750);
        Object parsed;
        try (InputStream input = connection.getInputStream()) {
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[2048]; int count;
            while ((count = input.read(buffer)) != -1) {
                if (bytes.size() + count > 65536) throw new IOException("Too many local climate views");
                bytes.write(buffer, 0, count);
            }
            parsed = de.vw.mib.jsonparser.MibJson.fromJson(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        } finally { connection.disconnect(); }
        Object rows = ((Map<?, ?>)parsed).get("data");
        if (!(rows instanceof List<?>)) throw new IOException("Invalid climate views response");
        for (Object item : (List<?>)rows) {
            Map<?, ?> row = (Map<?, ?>)item;
            String id = String.valueOf(row.get("id"));
            if (!"noView".equals(row.get("visibility")) || !id.matches("[0-9a-f-]{36}")) continue;
            connection = (HttpURLConnection)new URL(base + "/hvac/views/" + id).openConnection(java.net.Proxy.NO_PROXY);
            connection.setConnectTimeout(750); connection.setReadTimeout(750);
            connection.setRequestMethod("DELETE");
            try {
                int status = connection.getResponseCode();
                if (status != 200 && status != 204 && status != 404) throw new IOException("Climate view cleanup failed " + status);
                System.out.println(TAG + "retired closed climate view " + id);
            } finally { connection.disconnect(); }
        }
    }

    /** Exercise the configured ProxySelector from this same original HMI JVM.
     * This confirms JVM wiring only, not that every vendor HTTP stack obeys it.
     */
    private static void startGatewayProbe() {
        Thread probe = new Thread(new Runnable() {
            public void run() {
                try {
                    java.net.URL target = new java.net.URL("http://10.0.2.15:30001/gnss/simple/?$expand=1");
                    java.util.List<java.net.Proxy> selected = java.net.ProxySelector.getDefault().select(target.toURI());
                    int configured = Integer.parseInt(System.getProperty("http.proxyPort"));
                    boolean found = false;
                    for (java.net.Proxy candidate : selected) if (candidate.type() == java.net.Proxy.Type.HTTP
                            && candidate.address() instanceof java.net.InetSocketAddress
                            && ((java.net.InetSocketAddress) candidate.address()).getPort() == configured) found = true;
                    if (!found) throw new IllegalStateException("HMI JVM default ProxySelector did not select the guest gateway");
                    gatewayRead(target, null, "JVM_DEFAULT_PROXY_" + configured);
                    for (int port : new int[]{50006,50007})
                        gatewayRead(target, new java.net.Proxy(java.net.Proxy.Type.HTTP,
                            new java.net.InetSocketAddress("::1", port)), "JVM_EXPLICIT_PROXY_" + port);
                    System.out.println("HMI_GATEWAY_JVM_PROBE_PASS same_process=true external_network=false");
                } catch (Throwable error) {
                    System.out.println("HMI_GATEWAY_JVM_PROBE_FAILED " + error);
                    error.printStackTrace();
                }
            }
        }, "BenchGatewayProbe");
        probe.setDaemon(true);probe.start();
    }

    private static void gatewayRead(java.net.URL target, java.net.Proxy proxy, String label) throws Exception {
        java.net.HttpURLConnection connection = (java.net.HttpURLConnection)
            (proxy == null ? target.openConnection() : target.openConnection(proxy));
        connection.setConnectTimeout(8000);connection.setReadTimeout(8000);
        connection.setRequestProperty("User-Agent", "HMI-Bench-Gateway-Probe/1");
        try {
            int status = connection.getResponseCode();
            if (status != 200 || !"true".equals(connection.getHeaderField("X-HMI-Bench-Simulation")))
                throw new java.io.IOException("Unexpected simulated API status/identity: " + status);
            java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
            try (InputStream input = connection.getInputStream()) {
                byte[] buffer = new byte[1024];int n;
                while ((n=input.read(buffer)) != -1) {
                    if (output.size()+n>65536) throw new java.io.IOException("Probe response too large");
                    output.write(buffer,0,n);
                }
            }
            String body = new String(output.toByteArray(), StandardCharsets.UTF_8);
            if (!body.contains("satellite_cnt") || !body.contains("latitude"))
                throw new java.io.IOException("Missing GNSS fixture fields");
            System.out.println("HMI_GATEWAY_HTTP_OK route=" + label + " status=" + status + " simulation=true bytes=" + output.size());
        } finally { connection.disconnect(); }
    }

    public static synchronized void install() throws Exception {
        if (context != null) return;
        MIBOsgi framework = MIBOsgi.getInstance();
        context = new BundleInfo(89, "BenchExternalPlatform", BenchPlatform.class.getName());
        persistence = new Persistence(context);
        vehicle = new Vehicle(context);
        power = new Power(context);
        power.rsi.apply(power.started);
        keyPanel = new KeyPanel(context);
        register(DSIPersistence.class.getName(), persistence.endpoint);
        register(DSIGeneralVehicleStates.class.getName(), vehicle.endpoint);
        register(DSIPowerManagement.class.getName(), power.endpoint);
        register(DSIKeyPanel.class.getName(), keyPanel.endpoint);
        // No working IME backend exists on this bench. Publish explicit failure
        // callbacks instead of making startup wait for a provider that never comes.
        // This is not a Pinyin engine and never reports successful initialization.
        register(DSIAsiaInput.class.getName(), new UnavailableAsiaInput(context).endpoint);
        active = true;
        framework.addSubRegistry(new AdminInterception());
        Runtime.getRuntime().addShutdownHook(new Thread(new Runnable() {
            public void run() { uninstall(); }
        }, "BenchPlatformShutdown"));
        System.out.println(TAG + "registered DSIPersistence[0], DSIGeneralVehicleStates[0], DSIPowerManagement[0], DSIKeyPanel[0]");
        System.out.println(TAG + "registered DSIAsiaInput[0] capability=unavailable; initialization returns RESULTCODE_ERROR");
        String inputDevice = System.getProperty("bench.input.device");
        if (inputDevice != null) {
            if (!inputDevice.matches("/dev/vport[0-9]+p[0-9]+"))
                throw new IllegalArgumentException("bench.input.device must name a guest /dev/vportXpN");
            pointerReader = new PointerReader(inputDevice, keyPanel);
            pointerReader.start();
        }
    }

    public static synchronized void uninstall() {
        active = false;
        if (pointerReader != null) pointerReader.close();
        for (int i = REGISTRATIONS.size() - 1; i >= 0; --i) {
            try { REGISTRATIONS.get(i).unregister(); } catch (Exception ignored) { }
        }
        REGISTRATIONS.clear();
        System.out.println(TAG + "unregistered bench services");
    }

    private static void register(String type, Object service) {
        Hashtable<String, Object> properties = new Hashtable<String, Object>();
        properties.put(DSIBase.DEVICE_NAME, type);
        properties.put(DSIBase.DEVICE_INSTANCE, Integer.valueOf(INSTANCE));
        properties.put("bench.fixture", "stationary-qemu");
        REGISTRATIONS.add(context.registerService(type, service, properties));
    }

    /** Intercept only registered bench endpoints, delegating every other request. */
    private static final class AdminInterception implements SubRegistry {
        private final Map<Object, Object> wrappers = new IdentityHashMap<Object, Object>();
        public synchronized Object getService(String[] types, final Object service,
                                              Dictionary<String, Object> properties) {
            if (service instanceof ImageSPI) {
                Object cached = wrappers.get(service);
                if (cached == null) {
                    cached = Proxy.newProxyInstance(ImageSPI.class.getClassLoader(),
                        new Class<?>[]{ImageSPI.class}, new NamedSkinImages((ImageSPI)service));
                    wrappers.put(service, cached);
                }
                return cached;
            }
            if (!(service instanceof ServiceAdmin)) return service;
            Object cached = wrappers.get(service);
            if (cached != null) return cached;
            ServiceAdmin wrapper = new ServiceAdmin() {
                public boolean startService(String type, int instance) {
                    if (active && isBench(type, instance)) {
                        System.out.println(TAG + "ServiceAdmin.startService existing fixture " + type + "[0]");
                        return true;
                    }
                    return ((ServiceAdmin) service).startService(type, instance);
                }
                public boolean stopService(String type, int instance) {
                    if (active && isBench(type, instance)) return true;
                    return ((ServiceAdmin) service).stopService(type, instance);
                }
            };
            wrappers.put(service, wrapper);
            return wrapper;
        }
    }

    /** Bridge the native filename stub using the shipped skin's exact name map.
     * All pixels still come from the original numeric native image loader.
     */
    private static final class NamedSkinImages implements InvocationHandler {
        private final ImageSPI delegate;
        private final Map<Integer, Map<Long, Integer>> maps = new HashMap<Integer, Map<Long, Integer>>();
        NamedSkinImages(ImageSPI delegate) { this.delegate = delegate; }
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            if ("loadImageFromSkin".equals(method.getName()) && args[1] instanceof String) {
                int skin = ((Integer)args[0]).intValue();
                String filename = (String)args[1];
                Integer id = lookup(skin, filename);
                if (id != null) {
                    System.out.println(TAG + "skin filename mapped skin=" + skin + " name=" + filename + " id=" + id);
                    return delegate.loadImageFromSkin(skin, id.intValue());
                }
                System.out.println(TAG + "skin filename absent from original map skin=" + skin + " name=" + filename);
            }
            try { return method.invoke(delegate, args); }
            catch (java.lang.reflect.InvocationTargetException error) { throw error.getCause(); }
        }
        private synchronized Integer lookup(int skin, String filename) throws IOException {
            Map<Long, Integer> map = maps.get(Integer.valueOf(skin));
            if (map == null) {
                ConfigurationManager configuration = (ConfigurationManager)context.getService(
                    context.getServiceReference(ConfigurationManager.class.getName()));
                FileManager files = (FileManager)context.getService(
                    context.getServiceReference(FileManager.class.getName()));
                int handle = files.openContentFile(configuration.getSkinPath(skin) + "/imageidmap.mcf", 0,
                    de.vw.mib.graphics.image.SkinImageAssets.SKIN_IMAGE_CONTAINER_VERIFY);
                Object[] metadata;
                try { metadata = files.loadObjectArrayFromContentFile(handle, 42); }
                finally { files.closeContentFile(handle); }
                long[] names = (long[])metadata[1];
                int[] ids = (int[])metadata[2];
                if (names.length != ids.length) throw new IOException("Invalid original skin image name map");
                map = new HashMap<Long, Integer>();
                for (int i = 0; i < names.length; ++i) map.put(Long.valueOf(names[i]), Integer.valueOf(ids[i]));
                maps.put(Integer.valueOf(skin), map);
                System.out.println(TAG + "original skin name map skin=" + skin + " entries=" + names.length);
            }
            return map.get(Long.valueOf(new HashCodeBuilder().reset().append(filename).longHashCode()));
        }
    }

    private static boolean isBench(String type, int instance) {
        return instance == INSTANCE && (DSIPersistence.class.getName().equals(type)
                || DSIGeneralVehicleStates.class.getName().equals(type)
                || DSIPowerManagement.class.getName().equals(type) || DSIKeyPanel.class.getName().equals(type)
                || DSIAsiaInput.class.getName().equals(type));
    }

    /** Fail-fast capability endpoint: complete responses with explicit engine errors.
     * The stock IME needs absent native dependencies and car IPC. No candidate,
     * user dictionary or successful ready result is synthesized by this endpoint.
     */
    private static final class UnavailableAsiaInput extends Endpoint {
        private static final int ERROR = org.dsi.ifc.asiainput.Constants.RESULTCODE_ERROR;
        UnavailableAsiaInput(BundleContext context) {
            super(context, DSIAsiaInput.class, DSIAsiaInputListener.class);
        }
        void notification(Object attributes, Object target) { }
        Object request(String method, Object[] args) throws Exception {
            System.out.println(TAG + "asiainput unavailable: " + method);
            if ("initialize".equals(method)) emit("initialized", ERROR);
            else if ("setIntParameter".equals(method)) emit("setIntParameterResult", args[0], args[1], ERROR);
            else if ("setBooleanParameter".equals(method)) emit("setBooleanParameterResult", args[0], args[1], ERROR);
            else if ("setStringParameter".equals(method)) emit("setStringParameterResult", args[0], args[1], ERROR);
            else if ("setAdditionalWordDatabases".equals(method)) emit("setAdditionalWordDatabasesResult", ERROR);
            else if ("setUserDatabaseState".equals(method)) emit("setUserDatabaseStateResult", args[0], args[1], ERROR);
            else if ("resetToFactorySettings".equals(method)) emit("resetToFactorySettingsResult", ERROR);
            else {
                // The protocol has no error field on read/candidate callbacks.
                // Signal the explicit error rather than returning fictitious data.
                emit("indicateErrorStatus", org.dsi.ifc.asiainput.Constants.ERRORCODE_UNKNOWN);
            }
            return null;
        }
    }

    private abstract static class Endpoint implements InvocationHandler {
        final BundleContext context;
        final Class<?> listenerType;
        final Object endpoint;
        final List<Object> explicitListeners = Collections.synchronizedList(new ArrayList<Object>());
        Endpoint(BundleContext ctx, Class<?> type, Class<?> listenerType) {
            this.context = ctx;
            this.listenerType = listenerType;
            this.endpoint = Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[] {type}, this);
        }
        public final Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            String name = method.getName();
            if (method.getDeclaringClass() == Object.class) {
                if ("toString".equals(name)) return "BenchEndpoint(" + listenerType.getName() + ")";
                if ("hashCode".equals(name)) return Integer.valueOf(System.identityHashCode(proxy));
                if ("equals".equals(name)) return Boolean.valueOf(proxy == args[0]);
            }
            Object[] values = args == null ? new Object[0] : args;
            if ("setNotification".equals(name) || "clearNotification".equals(name)) {
                Object target = values[values.length - 1];
                if (target != null && listenerType.isInstance(target)) {
                    if ("setNotification".equals(name)) {
                        if (!explicitListeners.contains(target)) explicitListeners.add(target);
                        notification(values.length == 1 ? null : values[0], target);
                    } else {
                        explicitListeners.remove(target);
                        clearNotification(values.length == 1 ? null : values[0], target);
                    }
                }
                return null;
            }
            return request(name, values);
        }
        abstract Object request(String name, Object[] args) throws Exception;
        abstract void notification(Object attributes, Object target) throws Exception;
        void clearNotification(Object attributes, Object target) { }

        List<Object> listeners() throws Exception {
            List<Object> result = new ArrayList<Object>();
            String filter = "(&(objectClass=org.dsi.ifc.base.DSIListener)(DEVICE_NAME="
                    + listenerType.getName() + ")(DEVICE_INSTANCE=0))";
            ServiceReference[] references = context.getServiceReferences(null, filter);
            if (references != null) for (ServiceReference reference : references) {
                Object listener = context.getService(reference);
                if (listenerType.isInstance(listener) && !result.contains(listener)) result.add(listener);
                context.ungetService(reference);
            }
            return result;
        }
        void emit(String method, Object... args) throws Exception {
            if (method.startsWith("update") || method.startsWith("valueChanged")) {
                List<Object> subscribers;
                synchronized (explicitListeners) { subscribers = new ArrayList<Object>(explicitListeners); }
                for (Object listener : subscribers) emitTo(listener, method, args);
            } else {
                for (Object listener : listeners()) emitTo(listener, method, args);
            }
        }
        void emitTo(Object listener, String name, Object... args) throws Exception {
            for (Method method : listenerType.getMethods()) {
                if (method.getName().equals(name) && method.getParameterTypes().length == args.length) {
                    method.invoke(listener, args);
                    return;
                }
            }
            throw new NoSuchMethodException(listenerType.getName() + "." + name);
        }
        boolean wants(Object attrs, int attribute) {
            if (attrs == null) return true;
            if (attrs instanceof Integer) return ((Integer) attrs).intValue() == attribute;
            for (int value : (int[]) attrs) if (value == attribute) return true;
            return false;
        }
    }

    /** Deterministic typed RAM store; absent keys receive INVALID_KEY so HMI uses its defaults. */
    private static final class Persistence extends Endpoint {
        final Map<String, Object> values = Collections.synchronizedMap(new HashMap<String, Object>());
        Persistence(BundleContext context) {
            super(context, DSIPersistence.class, DSIPersistenceListener.class);
            // Original DiagnosisDataProvider reads this identification block.
            // Do not override ConfigurationManager or heartbeat output directly.
            values.put("Buffer:" + IDENTIFICATION_NAMESPACE + ":" + MU_SOFTWARE_VERSION_KEY,
                    MOCK_MU_SOFTWARE_VERSION.getBytes(StandardCharsets.US_ASCII));
            String codingPath = System.getProperty("bench.diagnosis.coding");
            if (codingPath != null) {
                if (!"/tmp/bench-ric-id3-coding.bin".equals(codingPath))
                    throw new IllegalArgumentException("Unexpected supplied coding path");
                try {
                    byte[] coding = readCoding(new FileInputStream(codingPath));
                    values.put("Buffer:33554432:1536", coding);
                    System.out.println(TAG + "supplied ID3 coding namespace=33554432 key=1536 bytes=" + coding.length);
                } catch (IOException error) { throw new IllegalStateException("Supplied ID3 coding unreadable", error); }
            }
            System.out.println(TAG + "diagnosis fixture namespace=" + IDENTIFICATION_NAMESPACE
                    + " key=" + MU_SOFTWARE_VERSION_KEY + " muSoftwareVersion=" + MOCK_MU_SOFTWARE_VERSION);
        }
        static byte[] readCoding(InputStream input) throws IOException {
            try {
                byte[] result = new byte[25];
                int offset = 0;
                while (offset < result.length) {
                    int count = input.read(result, offset, result.length - offset);
                    if (count < 0) throw new IOException("Supplied coding is shorter than 25 bytes");
                    offset += count;
                }
                if (input.read() != -1) throw new IOException("Supplied coding exceeds 25 bytes");
                return result;
            } finally { input.close(); }
        }
        String key(String type, Object[] args) { return type + ":" + args[0] + ":" + args[1]; }
        Object request(String method, Object[] args) throws Exception {
            System.out.println(TAG + "persistence." + method + " " + Arrays.deepToString(args));
            String normalized = method.endsWith("Timeout") ? method.substring(0, method.length() - 7) : method;
            if (normalized.startsWith("write") && args.length == 3) {
                String type = normalized.substring(5);
                values.put(key(type, args), copy(args[2]));
                emit(normalized, args[0], args[1], Integer.valueOf(DSIPersistence.ERRORCODE_NO_ERRORS));
            } else if (normalized.startsWith("read") && args.length >= 2) {
                String type = normalized.substring(4);
                Object value = values.get(key(type, args));
                int error = value == null ? DSIPersistence.ERRORCODE_INVALID_KEY : DSIPersistence.ERRORCODE_NO_ERRORS;
                if (value == null) {
                    if ("Int".equals(type)) value = Integer.valueOf(0);
                    else if ("String".equals(type)) value = "";
                    else if ("Buffer".equals(type)) value = new byte[0];
                    else if ("Array".equals(type)) value = new int[0];
                    else if ("StringArray".equals(type)) value = new String[0];
                    else throw new UnsupportedOperationException(normalized);
                }
                emit(normalized, args[0], args[1], copy(value), Integer.valueOf(error));
            } else if ("beginTransaction".equals(method) || "endTransaction".equals(method)) {
                emit(method, args[0], Integer.valueOf(0));
            } else if ("flushSQLDatabase".equals(method)) emit(method, Integer.valueOf(0));
            else if ("getVisibleSystemLanguages".equals(method)) emit(method, "en_US,zh_CN");
            else if ("setSQLDatabaseMedium".equals(method))
                emit("updateActiveSQLDatabaseMedium", args[0], Integer.valueOf(DSIListener.ATTRVALIDFLAG_VALID));
            else if ("unsubscribe".equals(method)) emit("unsubscribe", args[0], Integer.valueOf(0));
            else if (!("subscribe".equals(method) || "unsubscribeAll".equals(method)
                    || "enterEngineeringSession".equals(method) || "exitEngineeringSession".equals(method)))
                throw new UnsupportedOperationException("Unhandled persistence request: " + method);
            return null;
        }
        void notification(Object attrs, Object target) throws Exception {
            if (wants(attrs, DSIPersistence.ATTR_ACTIVESQLDATABASEMEDIUM))
                emitTo(target, "updateActiveSQLDatabaseMedium", Integer.valueOf(DSIPersistence.MEDIUM_RAM),
                        Integer.valueOf(DSIListener.ATTRVALIDFLAG_VALID));
        }
        Object copy(Object value) {
            if (value == null || !value.getClass().isArray()) return value;
            int length = Array.getLength(value);
            Object result = Array.newInstance(value.getClass().getComponentType(), length);
            System.arraycopy(value, 0, result, 0, length);
            return result;
        }
    }

    /** An explicitly parked bench fixture, with no vehicle hardware calls. */
    private static final class Vehicle extends Endpoint {
        final Map<Integer, Object[]> initial = new LinkedHashMap<Integer, Object[]>();
        Vehicle(BundleContext context) {
            super(context, DSIGeneralVehicleStates.class, DSIGeneralVehicleStatesListener.class);
            add(DSIGeneralVehicleStates.ATTR_DIMMEDHEADLIGHT, "updateDimmedHeadlight", false);
            add(DSIGeneralVehicleStates.ATTR_ACOUSTICPARKINGSYSTEM, "updateAcousticParkingSystem", false);
            add(DSIGeneralVehicleStates.ATTR_REVERSEGEAR, "updateReverseGear", false);
            add(DSIGeneralVehicleStates.ATTR_VEHICLESTANDSTILL, "updateVehicleStandstill", true);
            add(DSIGeneralVehicleStates.ATTR_PARKINGBRAKE, "updateParkingBrake", true);
            add(DSIGeneralVehicleStates.ATTR_DISPLAYDAYNIGHTDESIGN, "updateDisplayDayNightDesign", false);
            String[] suffixes = {"Car", "TV", "HDD", "BrowserSlideShow", "BrowserBordBook", "BrowserTravelAgent",
                    "BrowserWeb", "BWS", "Radiotext", "BTBonding", "Messaging", "DestinationInput"};
            for (String suffix : suffixes) try {
                int attr = DSIGeneralVehicleStates.class.getField("ATTR_" + suffix.toUpperCase(java.util.Locale.ROOT)
                        + "VELOCITYTHRESHOLD").getInt(null);
                add(attr, "update" + suffix + "VelocityThreshold", false);
            } catch (Exception error) { throw new IllegalStateException(error); }
            initial.put(Integer.valueOf(DSIGeneralVehicleStates.ATTR_PERSONALIZATIONSTATUS),
                    new Object[] {"updatePersonalizationStatus", Boolean.FALSE, Integer.valueOf(0), Integer.valueOf(1)});
        }
        void add(int attr, String update, boolean value) {
            initial.put(Integer.valueOf(attr), new Object[] {update, Boolean.valueOf(value), Integer.valueOf(1)});
        }
        Object request(String method, Object[] args) {
            System.out.println(TAG + "vehicle." + method + " " + Arrays.deepToString(args));
            return null;
        }
        void notification(Object attrs, Object listener) throws Exception {
            System.out.println(TAG + "vehicle.setNotification " + String.valueOf(attrs));
            for (Map.Entry<Integer, Object[]> entry : initial.entrySet()) {
                if (wants(attrs, entry.getKey().intValue())) {
                    Object[] event = entry.getValue();
                    emitTo(listener, (String) event[0], Arrays.copyOfRange(event, 1, event.length));
                }
            }
        }
    }

    /** External RSI signals used by the original car/climate power handler. */
    private static final class RsiVehicleState {
        final Map<String, String> paths = new LinkedHashMap<String, String>();
        final String base;
        RsiVehicleState(String base) { this.base = base; }
        static RsiVehicleState configured() throws IOException {
            RsiVehicleState result = new RsiVehicleState("http://[::1]:7770");
            String file = System.getProperty("bench.vehicle.rsi");
            if (file == null) return result; // Host self-test has no guest RSI process.
            if (!"/tmp/bench-vehicle.properties".equals(file)) throw new IOException("Unexpected vehicle configuration path");
            Properties properties = new Properties();
            try (InputStream input = new FileInputStream(file)) { properties.load(input); }
            for (String name : new String[] {"keyContact", "ignition", "comfortReadyState"}) {
                String path = properties.getProperty(name, "");
                if (!path.matches("/vehicledynamics/vehicleStates/[0-9a-f-]{36}"))
                    throw new IOException("Missing/invalid vehicle state: " + name);
                result.paths.put(name, path);
            }
            String power = properties.getProperty("power", "");
            if (!power.matches("/vehicledynamics/powermanagement/[0-9a-f-]{36}"))
                throw new IOException("Missing/invalid vehicle power state");
            result.paths.put("power", power);
            return result;
        }
        void apply(boolean started) throws IOException {
            for (Map.Entry<String, String> entry : paths.entrySet()) {
                String path = entry.getValue();
                String json = "power".equals(entry.getKey())
                    ? "{\"powerstates\":\"" + (started ? "on" : "standby") + "\"}"
                    : "{\"switchValue\":\"" + (started ? "ON" : "OFF") + "\"}";
                byte[] body = json.getBytes(StandardCharsets.US_ASCII);
                HttpURLConnection connection = (HttpURLConnection) new URL(base + path).openConnection(java.net.Proxy.NO_PROXY);
                connection.setConnectTimeout(750);
                connection.setReadTimeout(750);
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(body.length);
                try {
                    try (OutputStream output = connection.getOutputStream()) { output.write(body); }
                    int status = connection.getResponseCode();
                    if (status < 200 || status >= 300) throw new IOException("RSI vehicle update failed: " + status);
                    try (InputStream input = connection.getInputStream()) {
                        byte[] buffer = new byte[1024];
                        while (input.read(buffer) != -1) { }
                    }
                } finally { connection.disconnect(); }
            }
            if (!paths.isEmpty()) System.out.println(TAG + "VEHICLE_RSI_APPLIED started=" + started + " signals=" + paths.keySet());
        }
    }

    /** Stationary ignition simulation; original HMI decides which screen to show. */
    private static final class Power extends Endpoint {
        int lastOn = DSIPowerManagement.LASTON_MMI_ON;
        int childLock = DSIPowerManagement.CHILDLOCK_DISABLED;
        boolean started = !Boolean.getBoolean("bench.vehicle.control");
        final RsiVehicleState rsi;
        boolean powerObserved, clampObserved;
        volatile boolean rsiPowerObserved, rsiIgnitionObserved;
        boolean rsiObservationStarted;
        private void observe8704Power() {
            if (rsiObservationStarted) return;
            try {
                de.vw.mib.asl.clientapi.ASLClientAPIRegistry registry =
                    de.vw.mib.asl.framework.api.framework.ASLFrameworkFactory.getASLFrameworkAPI().getServices().getASLClientAPIRegistry();
                registry.createConnectedAPIPromise(de.vw.mib.asl.baseservice.powermanagement.PowerManagementAPI.class, 0).thenAccept(api -> {
                    api.attributes().subscribeHmiPowerState().thenAccept(value -> {
                        rsiPowerObserved = true;
                        synchronized (Power.this) { started = value.intValue() == 2; }
                        System.out.println(TAG + "8704_POWER_OBSERVED hmi=" + value);
                    });
                    api.attributes().subscribeIgnition().thenAccept(value -> {
                        rsiIgnitionObserved = true;
                        System.out.println(TAG + "8704_IGNITION_OBSERVED ignition=" + value);
                    });
                });
                rsiObservationStarted = true;
            } catch (RuntimeException notStartedYet) { /* Original services not published yet. */ }
        }
        final Map<Object, Set<Integer>> subscriptions = new IdentityHashMap<Object, Set<Integer>>();
        Power(BundleContext context) throws IOException {
            super(context, DSIPowerManagement.class, DSIPowerManagementListener.class);
            rsi = RsiVehicleState.configured();
        }
        Object request(String method, Object[] args) throws Exception {
            System.out.println(TAG + "power." + method + " " + Arrays.deepToString(args));
            if ("setChildLockRSE".equals(method)) {
                childLock = ((Integer) args[0]).intValue();
                emit("updateChildLockState", Integer.valueOf(childLock), Integer.valueOf(1));
            } else if ("setLastOn".equals(method)) {
                lastOn = ((Integer) args[0]).intValue();
                emit("updateLastOn", Integer.valueOf(lastOn), Integer.valueOf(1));
            } else if (method.startsWith("rebootSystem")) {
                System.out.println(TAG + "reboot request recorded; no hardware power operation in bench");
            }
            return null;
        }
        synchronized int state() {
            return started ? DSIPowerManagement.POWERSTATE_ON : DSIPowerManagement.POWERSTATE_STANDBY;
        }
        synchronized boolean ready() {
            if (Boolean.getBoolean("bench.input.8704") && !rsi.paths.isEmpty()) {
                observe8704Power();
                return rsiPowerObserved && rsiIgnitionObserved;
            }
            // Original RSI mode unsubscribes its DSI listener after connecting.
            // Readiness stays established across that deliberate handover.
            if (!rsi.paths.isEmpty()) return powerObserved && clampObserved;
            boolean clamp = false, supply = false;
            for (Set<Integer> attrs : subscriptions.values()) {
                clamp |= attrs == null || attrs.contains(DSIPowerManagement.ATTR_CLAMPSIGNAL);
                supply |= attrs == null || attrs.contains(DSIPowerManagement.ATTR_POWERMANAGEMENTSTATE);
            }
            return clamp && supply;
        }
        synchronized void setStarted(boolean value) throws Exception {
            if (started == value) return;
            try { rsi.apply(value); }
            catch (IOException failure) {
                try { rsi.apply(started); } catch (IOException rollback) { failure.addSuppressed(rollback); }
                throw failure;
            }
            started = value;
            int valid = DSIListener.ATTRVALIDFLAG_VALID;
            int event = value ? DSIPowerManagement.POWEREVENT_CLAMP_15_ON : DSIPowerManagement.POWEREVENT_CLAMP_15_OFF;
            for (Map.Entry<Object, Set<Integer>> entry : subscriptions.entrySet()) {
                Object listener = entry.getKey();
                Set<Integer> attrs = entry.getValue();
                if (attrs == null || attrs.contains(DSIPowerManagement.ATTR_CLAMPSIGNAL))
                    emitTo(listener, "updateClampSignal", new ClampSignal(value, value, false, false), valid);
                if (attrs == null || attrs.contains(DSIPowerManagement.ATTR_POWERMANAGEMENTSTATE))
                    emitTo(listener, "updatePowerManagementState", state(), event, valid);
                if (attrs == null || attrs.contains(DSIPowerManagement.ATTR_POWERMANAGEMENTSTATERIGHT))
                    emitTo(listener, "updatePowerManagementStateRight", state(), event, valid);
            }
            System.out.println(TAG + "VEHICLE_STATE started=" + started + " powerstate=" + state() + " clamp15=" + started);
        }
        synchronized void clearNotification(Object attrs, Object listener) { subscriptions.remove(listener); }
        synchronized void notification(Object attrs, Object listener) throws Exception {
            powerObserved |= wants(attrs, DSIPowerManagement.ATTR_POWERMANAGEMENTSTATE);
            clampObserved |= wants(attrs, DSIPowerManagement.ATTR_CLAMPSIGNAL);
            if (attrs == null) subscriptions.put(listener, null);
            else if (!subscriptions.containsKey(listener) || subscriptions.get(listener) != null) {
                Set<Integer> merged = subscriptions.get(listener);
                if (merged == null) merged = new HashSet<Integer>();
                if (attrs instanceof Integer) merged.add((Integer) attrs);
                else for (int attr : (int[]) attrs) merged.add(Integer.valueOf(attr));
                subscriptions.put(listener, merged);
            }
            System.out.println(TAG + "power.setNotification " + String.valueOf(attrs));
            int valid = DSIListener.ATTRVALIDFLAG_VALID;
            if (wants(attrs, DSIPowerManagement.ATTR_POWERMANAGEMENTSTATE))
                emitTo(listener, "updatePowerManagementState", state(),
                        DSIPowerManagement.POWEREVENT_OTHER, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_POWERMANAGEMENTSTATERIGHT))
                emitTo(listener, "updatePowerManagementStateRight", state(),
                        DSIPowerManagement.POWEREVENT_OTHER, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_CLAMPSIGNAL))
                emitTo(listener, "updateClampSignal", new ClampSignal(started, started, false, false), valid);
            if (wants(attrs, DSIPowerManagement.ATTR_RVCACTIVE))
                emitTo(listener, "updateRVCActive", false, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_BEMSTATE))
                emitTo(listener, "updateBEMState", DSIPowerManagement.BEMSTATE_0, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_CHILDLOCKSTATE))
                emitTo(listener, "updateChildLockState", childLock, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_LASTON)) emitTo(listener, "updateLastOn", lastOn, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_SPLASHSCREENANIMATION))
                emitTo(listener, "updateSplashScreenAnimation", DSIPowerManagement.ANIMATIONSTATUS_END, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_TELMAXPOPUP)) emitTo(listener, "updateTelMaxPopup", false, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_TSTANDBYPOPUP)) emitTo(listener, "updateTStandbyPopup", false, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_CLIMATECONTROLREQUEST))
                emitTo(listener, "updateClimateControlRequest", false, valid);
            if (wants(attrs, DSIPowerManagement.ATTR_NVEMFREIGABEONLINEUPDATE))
                emitTo(listener, "updateNvemFreigabeOnlineUpdate", false, valid);
        }
    }

    /** Logical panel configuration. Physical key/touch/character events are never synthesized. */
    private static final class KeyPanel extends Endpoint {
        final Map<String, Integer> settings = new HashMap<String, Integer>();
        int languageCode;
        String language = "";
        int recognizerMode = Constants.RECOGNIZERMODE_OFF;
        final Map<Object, Boolean> gestureSubscribers = new IdentityHashMap<Object, Boolean>();
        KeyPanel(BundleContext context) {
            super(context, DSIKeyPanel.class, DSIKeyPanelListener.class);
        }
        Object request(String method, Object[] args) throws Exception {
            System.out.println(TAG + "keypanel." + method + " " + Arrays.deepToString(args));
            if ("setRecognizerLanguage2".equals(method)) {
                language = (String) args[1]; languageCode = ((Integer) args[2]).intValue();
                emit("updateRecognizerLanguage2", args[0], language, languageCode, 1);
            } else if ("setRecognizerLanguage".equals(method)) {
                language = (String) args[1]; emit("updateRecognizerLanguage2", args[0], language, languageCode, 1);
            } else if ("setRecognizerMode".equals(method)) {
                recognizerMode = ((Integer) args[1]).intValue();
                emit("updateRecognizerMode", args[0], recognizerMode, 1);
            } else if ("setGenericSetting".equals(method)) {
                settings.put(args[0] + ":" + args[1], (Integer) args[2]);
                emit("genericSettingResponse", args[0], args[1], args[2]);
            } else if ("requestGenericSetting".equals(method)) {
                Integer value = settings.get(args[0] + ":" + args[1]);
                emit("genericSettingResponse", args[0], args[1], value == null ? Integer.valueOf(0) : value);
            } else if ("getProperty".equals(method)) {
                emit("getProperty", args[0], args[1], args[2], Constants.GETPROPERTYRESULT_DEVICE_NOT_READY, new byte[0]);
            } else if ("getVersionInfo".equals(method)) {
                emit("getVersionInfo", args[0], args[1], "BENCH_LOGICAL_PANEL");
            } else if ("requestLastKey".equals(method)) {
                emit("asyncException", Constants.GETPROPERTYRESULT_DEVICE_NOT_READY,
                        "No physical DSI panel key history in bench", DSIKeyPanel.RT_REQUESTLASTKEY);
            } else if ("resetDevice".equals(method)) {
                settings.clear(); recognizerMode = Constants.RECOGNIZERMODE_OFF;
            }
            return null;
        }
        void notification(Object attrs, Object listener) throws Exception {
            System.out.println(TAG + "keypanel.setNotification " + String.valueOf(attrs));
            if (wants(attrs, DSIKeyPanel.ATTR_GESTURE2)) synchronized (gestureSubscribers) {
                gestureSubscribers.put(listener, Boolean.TRUE);
            }
            if (wants(attrs, DSIKeyPanel.ATTR_INPUTPANELREADY))
                emitTo(listener, "updateInputPanelReady", 13, 1, DSIListener.ATTRVALIDFLAG_VALID);
            if (wants(attrs, DSIKeyPanel.ATTR_KEYBOARDTYPE))
                emitTo(listener, "updateKeyboardType", Constants.KBDTYPE_UNKONWN, DSIListener.ATTRVALIDFLAG_VALID);
            if (wants(attrs, DSIKeyPanel.ATTR_RECOGNIZERMODE))
                emitTo(listener, "updateRecognizerMode", 13, recognizerMode, DSIListener.ATTRVALIDFLAG_VALID);
            if (wants(attrs, DSIKeyPanel.ATTR_RECOGNIZERLANGUAGE2))
                emitTo(listener, "updateRecognizerLanguage2", 13, language, languageCode, DSIListener.ATTRVALIDFLAG_VALID);
        }
        void clearNotification(Object attrs, Object listener) {
            if (wants(attrs, DSIKeyPanel.ATTR_GESTURE2)) synchronized (gestureSubscribers) {
                gestureSubscribers.remove(listener);
            }
        }
        boolean pointer(int gesture, int fingers, int x, int y, int timeMs) throws Exception {
            List<Object> subscribers;
            synchronized (gestureSubscribers) { subscribers = new ArrayList<Object>(gestureSubscribers.keySet()); }
            if (subscribers.isEmpty()) return false;
            for (Object listener : subscribers)
                emitTo(listener, "updateGesture2", Constants.KBD_TOUCHSCREEN_FRONT, gesture, fingers,
                        false, x, y, 0, 0, timeMs, DSIListener.ATTRVALIDFLAG_VALID);
            return true;
        }
    }

    /** Stateful decoder for ordered physical pointer transitions from the viewer. */
    /** 8704 exposes InputManager devices, not the legacy 2995 DSI touch subscription. */
    private static final class NativeTouch implements de.vw.mib.input.devices.TouchHoverInputDevice {
        de.vw.mib.input.InputManager manager;
        de.vw.mib.input.devices.TouchHoverInputDeviceListener listener;
        public int getType() { return TYPE_TOUCH_HOVER_DEVICE; }
        public int getResolutionHorizontal() { return 1920; }
        public int getResolutionVertical() { return 1080; }
        public int getActiveAreaHorizontal() { return 286; }
        public int getActiveAreaVertical() { return 161; }
        public int getPixelDensity() { return 174; }
        public void addInputDeviceListener(de.vw.mib.input.devices.InputDeviceListener value) {
            if (value instanceof de.vw.mib.input.devices.TouchHoverInputDeviceListener)
                listener = (de.vw.mib.input.devices.TouchHoverInputDeviceListener)value;
        }
        public void removeInputDeviceListener(de.vw.mib.input.devices.InputDeviceListener value) {
            if (listener == value) listener = null;
        }
        synchronized boolean pointer(String action, int x, int y) {
            if (manager == null) {
                ServiceReference ref = context.getServiceReference(de.vw.mib.input.InputManager.class.getName());
                if (ref == null) return false;
                manager = (de.vw.mib.input.InputManager)context.getService(ref);
                if (manager == null) return false;
                manager.registerInputDevice(this);
                System.out.println(TAG + "8704_INPUT_DEVICE registered original InputManager");
            }
            if (listener == null) return false;
            int type = "down".equals(action) ? de.vw.mib.gesture.TouchPoint.PRESS :
                ("up".equals(action) ? de.vw.mib.gesture.TouchPoint.RELEASE : de.vw.mib.gesture.TouchPoint.DRAG);
            de.vw.mib.input.data.InputPointList points = manager.getInputPointListFactory().createInputPointList();
            points.add(manager.getInputPointFactory().createInputPoint(0, type, x, y, 0));
            listener.updateTouchInput(points, "up".equals(action) ? 0 : 1, 1, (int)(System.nanoTime()/1000000L));
            return true; // Original InputManager owns and disposes the pooled point list.
        }
    }

    private static final class PointerProtocol {
        private static final Pattern LINE = Pattern.compile("(down|move|up) ([0-9]{1,4}) ([0-9]{1,4})");
        private final KeyPanel panel;
        private boolean pressed;
        private long downNanos;
        PointerProtocol(KeyPanel panel) { this.panel = panel; }
        boolean accept(String line) throws Exception {
            Matcher m = LINE.matcher(line);
            if (!m.matches()) { reject("malformed line"); return false; }
            String action = m.group(1);
            int x = Integer.parseInt(m.group(2)), y = Integer.parseInt(m.group(3));
            if (x >= 1920 || y >= 1080) { reject("coordinates outside 1920x1080"); return false; }
            boolean down = action.equals("down"), up = action.equals("up");
            if (down == pressed) { reject(down ? "duplicate down" : "move/up without down"); return false; }
            long now = System.nanoTime();
            if (down) downNanos = now;
            int elapsed = (int) Math.min(Integer.MAX_VALUE, Math.max(0L, (now - downNanos) / 1000000L));
            int gesture = down ? Constants.GESTURE_PRESS : (up ? Constants.GESTURE_RELEASE : Constants.GESTURE_DRAG);
            if (!(Boolean.getBoolean("bench.input.8704") ? nativeTouch.pointer(action, x, y) : panel.pointer(gesture, up ? 0 : 1, x, y, elapsed))) {
                reject("no ATTR_GESTURE2 subscriber yet"); return false;
            }
            pressed = !up;
            System.out.println(TAG + "POINTER " + action + " " + x + " " + y + " -> DSI gesture=" + gesture);
            return true;
        }
        String vehicleReply(String line) throws Exception {
            Matcher command = Pattern.compile("vehicle ([0-9a-f]{16}) (status|start|stop)").matcher(line);
            if (!command.matches()) { reject("malformed vehicle command"); return null; }
            String state;
            if (!Boolean.getBoolean("bench.vehicle.control")) state = "disabled";
            else if (!power.ready()) state = "waiting";
            else {
                if (!"status".equals(command.group(2))) power.setStarted("start".equals(command.group(2)));
                state = power.started ? "started" : "stopped";
            }
            return "vehicle " + command.group(1) + " " + state + "\n";
        }
        void read(InputStream input) throws Exception { read(input, null); }
        void read(InputStream input, OutputStream output) throws Exception {
            byte[] line = new byte[64]; int length = 0; boolean invalid = false;
            int value;
            while ((value = input.read()) >= 0) {
                if (value == '\n') {
                    if (!invalid) {
                        if (length > 0 && line[length - 1] == '\r') length--;
                        String message = new String(line, 0, length, StandardCharsets.US_ASCII);
                        if (message.startsWith("vehicle ") && output != null) {
                            String reply = vehicleReply(message);
                            if (reply != null) { output.write(reply.getBytes(StandardCharsets.US_ASCII)); output.flush(); }
                        } else accept(message);
                    } else reject("overlong/non-ASCII line");
                    length = 0; invalid = false;
                } else if (value > 127 || value == 0 || length == line.length) invalid = true;
                else if (!invalid) line[length++] = (byte) value;
            }
            // No event is invented for EOF, a partial line, or a transport failure.
            if (length != 0 || invalid) reject("incomplete line at EOF");
        }
        void reject(String reason) { System.out.println(TAG + "POINTER_REJECT " + reason); }
    }

    private static final class PointerReader extends Thread {
        private final String device;
        private final PointerProtocol protocol;
        private volatile boolean closed;
        private volatile InputStream stream;
        PointerReader(String device, KeyPanel panel) {
            super("BenchPhysicalPointer"); this.device = device; this.protocol = new PointerProtocol(panel); setDaemon(true);
        }
        public void run() {
            System.out.println(TAG + "pointer reader waiting on " + device);
            while (!closed && active) {
                // virtio-console permits a single open: share one O_RDWR descriptor.
                try (RandomAccessFile channel = new RandomAccessFile(device, "rw");
                     InputStream opened = new FileInputStream(channel.getFD());
                     OutputStream replies = new FileOutputStream(channel.getFD())) {
                    stream = opened;
                    protocol.read(opened, replies);
                    if (!closed) System.out.println(TAG + "pointer transport EOF; waiting for reconnection");
                } catch (Exception error) {
                    if (!closed) System.out.println(TAG + "pointer transport: " + error.toString());
                } finally { stream = null; }
                try { Thread.sleep(500); } catch (InterruptedException interrupted) { break; }
            }
        }
        void close() {
            closed = true; interrupt(); InputStream current = stream;
            if (current != null) try { current.close(); } catch (IOException ignored) { }
        }
    }

    private static void selfTestRsi() throws Exception {
        final List<String> requests = Collections.synchronizedList(new ArrayList<String>());
        final java.util.concurrent.atomic.AtomicBoolean fail = new java.util.concurrent.atomic.AtomicBoolean();
        com.sun.net.httpserver.HttpServer server = com.sun.net.httpserver.HttpServer.create(
                new java.net.InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/vehicledynamics/", exchange -> {
            try {
                java.io.ByteArrayOutputStream body = new java.io.ByteArrayOutputStream();
                try (InputStream input = exchange.getRequestBody()) {
                    byte[] buffer = new byte[256]; int count;
                    while ((count = input.read(buffer)) != -1) body.write(buffer, 0, count);
                }
                requests.add(exchange.getRequestMethod() + " " + exchange.getRequestURI()
                        + " " + new String(body.toByteArray(), StandardCharsets.US_ASCII));
                exchange.sendResponseHeaders(fail.get() ? 503 : 200, -1);
            } finally { exchange.close(); }
        });
        final List<String> deletedViews = new ArrayList<String>();
        server.createContext("/hvac/views/", exchange -> {
            try {
                if ("DELETE".equals(exchange.getRequestMethod())) {
                    deletedViews.add(exchange.getRequestURI().getPath());
                    exchange.sendResponseHeaders(204, -1);
                } else {
                    String body = "{\"data\":[{\"id\":\"11111111-1111-1111-1111-111111111111\",\"visibility\":\"noView\"},"
                        + "{\"id\":\"22222222-2222-2222-2222-222222222222\",\"visibility\":\"requestedViewVisible\"},"
                        + "{\"id\":\"33333333-3333-3333-3333-333333333333\",\"visibility\":\"newRequest\"},"
                        + "{\"id\":\"../invalid\",\"visibility\":\"noView\"}]}";
                    byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(200, bytes.length);
                    exchange.getResponseBody().write(bytes);
                }
            } finally { exchange.close(); }
        });
        server.start();
        try {
            RsiVehicleState rsi = new RsiVehicleState("http://127.0.0.1:" + server.getAddress().getPort());
            for (String name : new String[] {"keyContact", "ignition", "comfortReadyState"})
                rsi.paths.put(name, "/vehicledynamics/vehicleStates/" + name);
            rsi.paths.put("power", "/vehicledynamics/powermanagement/power");
            rsi.apply(true); rsi.apply(false);
            require(requests.size() == 8, "start and stop update all external RSI signals");
            require(requests.get(2).endsWith("{\"switchValue\":\"ON\"}")
                    && requests.get(3).endsWith("{\"powerstates\":\"on\"}")
                    && requests.get(6).endsWith("{\"switchValue\":\"OFF\"}")
                    && requests.get(7).endsWith("{\"powerstates\":\"standby\"}"),
                    "comfort readiness, ignition and power use original RSI enum values");
            fail.set(true);
            boolean rejected = false;
            try { rsi.apply(true); } catch (IOException expected) { rejected = true; }
            require(rejected, "failed RSI write cannot report a successful vehicle transition");
            cleanupClimateViews("http://127.0.0.1:" + server.getAddress().getPort());
            require(deletedViews.equals(Arrays.asList("/hvac/views/11111111-1111-1111-1111-111111111111")),
                "only closed climate views are retired; active requests and invalid IDs are preserved");
        } finally { server.stop(0); }
    }

    private static void selfTest() throws Exception {
        selfTestRsi();
        require(Arrays.equals(new byte[25], Persistence.readCoding(new ByteArrayInputStream(new byte[25]))), "coding exact bytes");
        for (int badSize : new int[]{24, 26}) {
            boolean rejected = false;
            try { Persistence.readCoding(new ByteArrayInputStream(new byte[badSize])); }
            catch (IOException expected) { rejected = true; }
            require(rejected, "coding exact length " + badSize);
        }
        install();
        selfTestNamedImages();
        final List<String> callbacks = new ArrayList<String>();
        DSIPersistenceListener listener = (DSIPersistenceListener) Proxy.newProxyInstance(
                DSIPersistenceListener.class.getClassLoader(), new Class<?>[] {DSIPersistenceListener.class},
                new InvocationHandler() {
                    public Object invoke(Object p, Method m, Object[] a) {
                        if (m.getName().equals("equals")) return Boolean.valueOf(p == a[0]);
                        if (m.getName().equals("hashCode")) return Integer.valueOf(System.identityHashCode(p));
                        if (m.getName().equals("toString")) return "BenchTestListener";
                        callbacks.add(m.getName() + Arrays.deepToString(a));
                        return null;
                    }
                });
        Hashtable<String, Object> props = new Hashtable<String, Object>();
        props.put(DSIBase.DEVICE_NAME, DSIPersistenceListener.class.getName());
        props.put(DSIBase.DEVICE_INSTANCE, Integer.valueOf(0));
        REGISTRATIONS.add(context.registerService(DSIListener.class.getName(), listener, props));
        ServiceReference[] refs = context.getServiceReferences(DSIPersistence.class.getName(), "(DEVICE_INSTANCE=0)");
        require(refs != null && refs.length == 1, "one original-registry persistence service");
        DSIPersistence api = (DSIPersistence) context.getService(refs[0]);
        final List<String> persistenceUpdates = new ArrayList<String>();
        api.setNotification(DSIPersistence.ATTR_ACTIVESQLDATABASEMEDIUM,
                (DSIPersistenceListener) recorder(DSIPersistenceListener.class, persistenceUpdates));
        api.setSQLDatabaseMedium(DSIPersistence.MEDIUM_FLASH);
        api.readInt(9, 111L);
        api.writeInt(9, 111L, 77);
        api.readIntTimeout(9, 111L, 100);
        byte[] bytes = new byte[] {1, 2, 3};
        api.writeBuffer(4, 2L, bytes); bytes[0] = 9; api.readBuffer(4, 2L);
        api.writeString(4, 3L, "bench"); api.readString(4, 3L);
        api.writeArray(4, 4L, new int[] {5, 6}); api.readArray(4, 4L);
        api.writeStringArray(4, 5L, new String[] {"zh_CN"}); api.readStringArray(4, 5L);
        api.beginTransaction(4); api.endTransaction(4, true);
        require(callbacks.contains("readInt[9, 111, 0, 2]"), "missing key reports INVALID_KEY");
        require(callbacks.contains("readInt[9, 111, 77, 0]"), "typed write/read and timeout response");
        require(callbacks.contains("readBuffer[4, 2, [1, 2, 3], 0]"), "defensive buffer copy");
        api.readBuffer(IDENTIFICATION_NAMESPACE, MU_SOFTWARE_VERSION_KEY);
        require(callbacks.contains("readBuffer[50331648, 61833, [77, 79, 67, 75], 0]"),
                "original diagnostic identification read returns four-byte MOCK MU version");
        api.readBuffer(IDENTIFICATION_NAMESPACE, 61831L);
        require(callbacks.contains("readBuffer[50331648, 61831, [], 2]"),
                "MU fixture does not invent unrelated diagnostic identification fields");
        require(callbacks.contains("readString[4, 3, bench, 0]"), "string round trip");
        require(callbacks.contains("readArray[4, 4, [5, 6], 0]"), "array round trip");
        require(callbacks.contains("readStringArray[4, 5, [zh_CN], 0]"), "string-array round trip");
        require(persistenceUpdates.contains("updateActiveSQLDatabaseMedium[1, 1]"),
                "persistence update reaches notification subscriber");
        for (String update : persistenceUpdates) require(update.startsWith("update"),
                "notification skeleton receives updates only");
        for (String response : callbacks) require(!response.startsWith("update"),
                "response skeleton excludes updates");
        final List<String> vehicleEvents = new ArrayList<String>();
        DSIGeneralVehicleStatesListener vlistener = new DSIGeneralVehicleStatesListener() {
            public void updateVehicleStandstill(boolean value, int validity) { vehicleEvents.add("standstill=" + value + "/" + validity); }
            public void updateParkingBrake(boolean value, int validity) { vehicleEvents.add("parkingBrake=" + value + "/" + validity); }
            public void updateReverseGear(boolean value, int validity) { vehicleEvents.add("reverse=" + value + "/" + validity); }
        };
        ((DSIGeneralVehicleStates) vehicle.endpoint).setNotification(new int[] {
                DSIGeneralVehicleStates.ATTR_VEHICLESTANDSTILL, DSIGeneralVehicleStates.ATTR_PARKINGBRAKE,
                DSIGeneralVehicleStates.ATTR_REVERSEGEAR}, vlistener);
        require(vehicleEvents.contains("standstill=true/1") && vehicleEvents.contains("parkingBrake=true/1")
                && vehicleEvents.contains("reverse=false/1"), "stationary vehicle notifications");
        final List<String> powerEvents = new ArrayList<String>();
        DSIPowerManagementListener plistener = (DSIPowerManagementListener)
                recorder(DSIPowerManagementListener.class, powerEvents);
        ServiceReference pref = context.getServiceReference(DSIPowerManagement.class.getName());
        DSIPowerManagement powerApi = (DSIPowerManagement) context.getService(pref);
        powerApi.setNotification(plistener);
        require(powerEvents.contains("updatePowerManagementState[1, 7, 1]")
                && powerEvents.contains("updateRVCActive[false, 1]")
                && powerEvents.contains("updateSplashScreenAnimation[2, 1]"),
                "registered power endpoint publishes on, non-RVC, completed splash");
        require(powerEvents.toString().contains("clampS=true") && powerEvents.toString().contains("clamp15=true"),
                "power notification uses typed ignition-on ClampSignal");
        System.setProperty("bench.vehicle.control", "true");
        PointerProtocol vehicleProtocol = new PointerProtocol(keyPanel);
        java.io.ByteArrayOutputStream replies = new java.io.ByteArrayOutputStream();
        vehicleProtocol.read(new ByteArrayInputStream(("vehicle 0123456789abcdef stop\n"
                + "vehicle 0123456789abcdee status\nvehicle 0123456789abcded start\n"
                + "vehicle 0123456789abcdec arbitrary\n").getBytes(StandardCharsets.US_ASCII)), replies);
        String vehicleReplies = new String(replies.toByteArray(), StandardCharsets.US_ASCII);
        require(vehicleReplies.equals("vehicle 0123456789abcdef stopped\nvehicle 0123456789abcdee stopped\n"
                + "vehicle 0123456789abcded started\n"), "vehicle commands acknowledge actual adapter state; invalid commands rejected");
        require(powerEvents.contains("updatePowerManagementState[2, 3, 1]")
                && powerEvents.contains("updatePowerManagementState[1, 2, 1]"), "ignition transitions emit original typed power states");
        require(powerEvents.toString().contains("clampS=false,clamp15=false"), "stop removes ignition clamp signals");
        System.clearProperty("bench.vehicle.control");
        // RSI handover intentionally clears DSI notifications; control remains usable.
        power.rsi.paths.put("ignition", "/vehicledynamics/vehicleStates/00000000-0000-0000-0000-000000000001");
        powerApi.clearNotification(plistener);
        require(power.ready(), "original RSI handover does not revoke established readiness");
        power.rsi.paths.clear();
        final List<String> keyEvents = new ArrayList<String>();
        final List<String> keyResponseEvents = new ArrayList<String>();
        DSIKeyPanelListener klistener = (DSIKeyPanelListener) recorder(DSIKeyPanelListener.class, keyEvents);
        DSIKeyPanelListener kresponse = (DSIKeyPanelListener) recorder(DSIKeyPanelListener.class, keyResponseEvents);
        Hashtable<String, Object> kprops = new Hashtable<String, Object>();
        kprops.put(DSIBase.DEVICE_NAME, DSIKeyPanelListener.class.getName());
        kprops.put(DSIBase.DEVICE_INSTANCE, Integer.valueOf(0));
        REGISTRATIONS.add(context.registerService(DSIListener.class.getName(), kresponse, kprops));
        ServiceReference kref = context.getServiceReference(DSIKeyPanel.class.getName());
        DSIKeyPanel keyApi = (DSIKeyPanel) context.getService(kref);
        keyApi.setNotification(klistener);
        keyApi.setRecognizerLanguage2(13, "zh_CN", 20);
        keyApi.setRecognizerMode(13, Constants.RECOGNIZERMODE_OFF);
        keyApi.setGenericSetting(13, 5, 7);
        keyApi.requestGenericSetting(13, 5);
        keyApi.getProperty(13, 1, 2);
        keyApi.requestLastKey(13);
        require(keyEvents.contains("updateInputPanelReady[13, 1, 1]")
                && keyEvents.contains("updateRecognizerLanguage2[13, zh_CN, 20, 1]")
                && keyResponseEvents.contains("genericSettingResponse[13, 5, 7]"),
                "registered keypanel endpoint serves control-plane configuration");
        require(keyResponseEvents.contains("getProperty[13, 1, 2, 6, []]"), "unavailable panel property reports explicit error");
        for (String response : keyResponseEvents) require(!response.startsWith("update"),
                "keypanel response skeleton excludes update methods");
        for (String update : keyEvents) require(update.startsWith("update"),
                "keypanel notification skeleton receives update methods only");
        for (String event : keyEvents)
            require(!(event.startsWith("updateKey2") || event.startsWith("updateEncoder2")
                    || event.startsWith("updateCharacterEvent2") || event.startsWith("updateGesture2")
                    || event.startsWith("updateTouchInput") || event.startsWith("lastKey[")),
                    "no synthesized physical input: " + event.split("\\[")[0]);
        final List<String> pointerEvents = new ArrayList<String>();
        DSIKeyPanelListener pointerListener = (DSIKeyPanelListener) recorder(DSIKeyPanelListener.class, pointerEvents);
        keyApi.setNotification(DSIKeyPanel.ATTR_GESTURE2, pointerListener);
        PointerProtocol protocol = new PointerProtocol(keyPanel);
        String wire = "move 1 2\nup 1 2\ndown -1 2\ndown 1920 2\ntap 1 2\n"
                + "down 50 60\nmove 51 62\nup 51 62\n";
        protocol.read(new ByteArrayInputStream(wire.getBytes(StandardCharsets.US_ASCII)));
        require(pointerEvents.size() == 3, "strict pointer transport rejects invalid/orphan messages");
        require(pointerEvents.get(0).startsWith("updateGesture2[13, 4, 1, false, 50, 60, 0, 0,")
                && pointerEvents.get(1).startsWith("updateGesture2[13, 5, 1, false, 51, 62, 0, 0,")
                && pointerEvents.get(2).startsWith("updateGesture2[13, 3, 0, false, 51, 62, 0, 0,"),
                "pointer down/move/up route as typed original DSI press/drag/release");
        protocol.read(new ByteArrayInputStream("down 60 70".getBytes(StandardCharsets.US_ASCII)));
        require(pointerEvents.size() == 3, "partial EOF does not invent input");
        keyApi.clearNotification(DSIKeyPanel.ATTR_GESTURE2, pointerListener);
        protocol.accept("down 70 80");
        require(pointerEvents.size() == 3, "cleared gesture subscription receives no further physical input");
        PointerProtocol notReady = new PointerProtocol(new KeyPanel(context));
        require(!notReady.accept("down 1 2"), "pointer press is dropped before a gesture subscriber exists");
        System.out.println(TAG + "SELF_TEST_POINTER_TRANSPORT " + pointerEvents);
        final int[] delegateCalls = new int[1];
        ServiceAdmin underlying = new ServiceAdmin() {
            public boolean startService(String name, int instance) { delegateCalls[0]++; return false; }
            public boolean stopService(String name, int instance) { delegateCalls[0]++; return false; }
        };
        register(ServiceAdmin.class.getName(), underlying);
        ServiceReference adminRef = context.getServiceReference(ServiceAdmin.class.getName());
        ServiceAdmin admin = (ServiceAdmin) context.getService(adminRef);
        require(admin.startService(DSIPersistence.class.getName(), 0) && delegateCalls[0] == 0,
                "original SubRegistry intercepts only bench service");
        require(!admin.startService("example.other.Service", 0) && delegateCalls[0] == 1,
                "other services delegate unchanged");
        final List<String> asiaResponses = new ArrayList<String>();
        DSIAsiaInputListener asiaListener = (DSIAsiaInputListener) recorder(DSIAsiaInputListener.class, asiaResponses);
        Hashtable<String, Object> asiaProps = new Hashtable<String, Object>();
        asiaProps.put(DSIBase.DEVICE_NAME, DSIAsiaInputListener.class.getName());
        asiaProps.put(DSIBase.DEVICE_INSTANCE, Integer.valueOf(0));
        REGISTRATIONS.add(context.registerService(DSIListener.class.getName(), asiaListener, asiaProps));
        ServiceReference asiaRef = context.getServiceReference(DSIAsiaInput.class.getName());
        DSIAsiaInput asiaApi = (DSIAsiaInput) context.getService(asiaRef);
        require(admin.startService(DSIAsiaInput.class.getName(), 0) && delegateCalls[0] == 1,
                "unavailable capability is a registered endpoint, not a false admin-only ready response");
        asiaApi.initialize(org.dsi.ifc.asiainput.Constants.LANGUAGE_CHINA);
        asiaApi.setIntParameter(org.dsi.ifc.asiainput.Constants.PARAMETER_INPUTMODE,
                org.dsi.ifc.asiainput.Constants.INPUTMODE_PINYIN);
        asiaApi.buildCandidates();
        require(asiaResponses.contains("initialized[1]") && !asiaResponses.contains("initialized[0]")
                && asiaResponses.contains("setIntParameterResult[1, 2, 1]")
                && asiaResponses.contains("indicateErrorStatus[0]"), "absent IME fails explicitly without fabricated candidates");
        System.out.println(TAG + "CALLBACKS " + callbacks);
        System.out.println(TAG + "VEHICLE " + vehicleEvents);
        System.out.println(TAG + "POWER " + powerEvents);
        System.out.println(TAG + "KEYPANEL " + keyEvents);
        System.out.println(TAG + "KEYPANEL_RESPONSES " + keyResponseEvents);
        uninstall();
        require(!admin.startService(DSIPersistence.class.getName(), 0) && delegateCalls[0] == 2,
                "rollback disables ServiceAdmin interception");
        refs = context.getServiceReferences(DSIPersistence.class.getName(), "(DEVICE_INSTANCE=0)");
        require(refs == null || refs.length == 0, "rollback unregisters original-registry service");
        System.out.println(TAG + "SELF_TEST_PASS");
    }

    private static void selfTestNamedImages() throws Exception {
        String filename = "supplied/Climate/Interior_0.png";
        final Object[] metadata = new Object[]{new int[]{0, 0},
            new long[]{new HashCodeBuilder().reset().append(filename).longHashCode()}, new int[]{321}};
        final List<String> imageCalls = new ArrayList<String>();
        NamedSkinImages adapter = new NamedSkinImages((ImageSPI)Proxy.newProxyInstance(
            ImageSPI.class.getClassLoader(), new Class<?>[]{ImageSPI.class}, (p,m,a) -> {
                imageCalls.add(m.getName() + Arrays.deepToString(a));
                if (a != null && a.length == 2 && a[1] instanceof String) throw new IOException("native filename unsupported");
                return null;
            }));
        ServiceRegistration cfg = context.registerService(ConfigurationManager.class.getName(),
            Proxy.newProxyInstance(ConfigurationManager.class.getClassLoader(), new Class<?>[]{ConfigurationManager.class},
                (p,m,a) -> "getSkinPath".equals(m.getName()) ? "/original/skin" : null), null);
        ServiceRegistration files = context.registerService(FileManager.class.getName(),
            Proxy.newProxyInstance(FileManager.class.getClassLoader(), new Class<?>[]{FileManager.class}, (p,m,a) -> {
                if ("openContentFile".equals(m.getName())) return Integer.valueOf(1);
                if ("loadObjectArrayFromContentFile".equals(m.getName())) return metadata;
                return null;
            }), null);
        ImageSPI api = (ImageSPI)Proxy.newProxyInstance(ImageSPI.class.getClassLoader(), new Class<?>[]{ImageSPI.class}, adapter);
        try {
            api.loadImageFromSkin(0, filename);
            api.loadImageFromSkin(0, 55);
            boolean missing = false;
            try { api.loadImageFromSkin(0, "missing.png"); } catch (IOException expected) { missing = true; }
            require(imageCalls.equals(Arrays.asList("loadImageFromSkin[0, 321]", "loadImageFromSkin[0, 55]",
                "loadImageFromSkin[0, missing.png]")) && missing,
                "exact packaged filename maps to numeric image; native IDs and missing-asset errors are preserved");
        } finally { files.unregister(); cfg.unregister(); }
    }

    private static Object recorder(Class<?> type, final List<String> events) {
        return Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[] {type}, new InvocationHandler() {
            public Object invoke(Object p, Method m, Object[] a) {
                if (m.getName().equals("equals")) return Boolean.valueOf(p == a[0]);
                if (m.getName().equals("hashCode")) return Integer.valueOf(System.identityHashCode(p));
                if (m.getName().equals("toString")) return "BenchTestRecorder";
                events.add(m.getName() + Arrays.deepToString(a));
                return null;
            }
        });
    }

    private static void require(boolean condition, String text) {
        if (!condition) throw new AssertionError(text);
        System.out.println(TAG + "CHECK_PASS " + text);
    }
}
