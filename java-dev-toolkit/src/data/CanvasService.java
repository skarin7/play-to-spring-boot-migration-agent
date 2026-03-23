package com.phenompeople.siteedits.services;

import javax.inject.Inject;
import javax.inject.Singleton;
import com.phenompeople.siteedits.models.*;
import org.apache.commons.lang3.RandomStringUtils;
import org.apache.commons.lang3.StringUtils;
import org.bson.Document;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Attribute;
import org.jsoup.nodes.Element;
import org.jsoup.nodes.Node;
import org.jsoup.nodes.TextNode;
import org.jsoup.parser.Tag;
import org.jsoup.select.Elements;
import static com.phenompeople.siteedits.utils.Constants.CONFIG.MONGO_DB_PREPROD;
import static com.phenompeople.siteedits.utils.Constants.DATA.*;
import static com.phenompeople.siteedits.utils.Constants.STATUS.*;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.google.common.collect.ImmutableMap;
import com.phenompeople.siteedits.db.MongoManager;
import com.phenompeople.siteedits.db.PreProdMongoManager;
import com.phenompeople.siteedits.db.RedisManager;
import com.phenompeople.siteedits.db.S3Manager;
import com.phenompeople.siteedits.db.StorageManager;
import com.phenompeople.siteedits.exception.PhenomException;
import com.phenompeople.siteedits.utils.Constants;
import com.phenompeople.siteedits.utils.HtmlParser;
import com.phenompeople.siteedits.utils.Log;
import com.phenompeople.siteedits.utils.MongoConstansts;
import com.phenompeople.siteedits.utils.PhenomConfig;
import com.phenompeople.siteedits.utils.RedisKeyUtil;
import com.phenompeople.siteedits.utils.SiteUtil;
import com.typesafe.config.Config;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import play.Logger;
import play.libs.Json;
import play.mvc.Http;

@Singleton
public class CanvasServiceOriginalOriginalOriginalOriginalOriginalOriginal implements Constants, MongoConstansts {

    private final Logger.ALogger logger = Log.getLogger(CanvasService.class);

    private static final ObjectMapper mapper = Json.mapper();

    private PhenomConfig conf;

    String db;

    String preprodDb;

    @Inject
    WidgetUtil widgetUtil;

    @Inject
    WidgetsService ws;

    @Inject
    SiteUtil siteUtil;

    @Inject
    MongoManager mongoManager;

    @Inject
    RedisManager redisManager;

    @Inject
    StorageManager storageManager;

    @Inject
    RemoteServiceCaller remoteServiceCaller;

    @Inject
    ContentService contentService;

    @Inject
    PreProdMongoManager preProdMongoManager;

    @Inject
    S3Manager s3Manager;

    @Inject
    PresetService presetService;

    private Timer timer = new Timer();

    @Inject
    CanvasServiceOriginalOriginalOriginalOriginalOriginalOriginal(PhenomConfig conf) {
        this.conf = conf;
        this.db = conf.getString(MONGO_DB);
        this.preprodDb = conf.hasPath(MONGO_DB_PREPROD) ? conf.getString(MONGO_DB_PREPROD) : db;
    }

    public Map<String, Object> addWidget(CanvasDragDropRequest canvasDragDropRequest, Http.Request request) {
        Map<String, Object> response = new HashMap<>();
        String structure = canvasDragDropRequest.getStructure();
        Element widgetEle = widgetUtil.getCanvasWidget(structure);
        String instanceId;
        String css = null;
        if (!widgetEle.hasAttr(INSTANCE_ID_FIELD)) {
            instanceId = SiteUtil.generateUniqueId();
            widgetEle.attr(INSTANCE_ID_FIELD, instanceId);
        } else {
            instanceId = widgetEle.attr(INSTANCE_ID_FIELD);
        }
        if (widgetEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
            logger.info("Adding static widget {}", widgetEle.toString());
            String widgetId = widgetEle.attr(CANVAS_STATIC_WIDGET_ATTR);
            org.bson.Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
            if (widgetDoc == null) {
                response.put(STATUS_KEY, false);
                response.put("message", "widget doc not found for" + widgetId);
                return response;
            }
            Document viewDoc = getCanvasViewDocument(widgetDoc.getString(VIEW_ID));
            if (viewDoc == null) {
                response.put(STATUS_KEY, false);
                response.put("message", "view doc not found for" + widgetDoc.getString(VIEW_ID));
                return response;
            }
            String viewHtml = viewDoc.getString("viewHtml");
            if (canvasDragDropRequest.getViewHtml() != null) {
                viewHtml = canvasDragDropRequest.getViewHtml();
            }
            String version = viewDoc.containsKey("importNumber") ? Integer.toString(viewDoc.getInteger("importNumber")) : null;
            widgetEle.attr(VIEW_REVNO, version);
            //            Document settingsDoc = mongoManager.findDocument(CANVAS_SETTINGS, db, new org.bson.Document(SETTING_NAME, widgetDoc.getString(SETTING_NAME)));
            //            if(settingsDoc == null) {
            //                response.put(STATUS_KEY, false);
            //                response.put("message", "settings doc not found for" + widgetDoc.getString(SETTING_NAME));
            //                return response ;
            //            }
            Element pageEle = widgetEle.clone();
            widgetEle.html(viewHtml);
            canvasDragDropRequest.setStructure(pageEle.toString());
            //            enhanceRepeaters(widgetEle);
            //            widgetEle.getAllElements().forEach(ele -> {
            //                if (ele.hasAttr("data-ps")) {
            //                    ele.attr("data-ps", instanceId + "-" + ele.attr("data-ps"));
            //                }
            //            });
            Map<String, Object> contentMap = new HashMap<>();
            //            enhanceTagSettings(widgetEle, widgetDoc.getString(SETTING_NAME));
            generateContentData(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getPageId(), instanceId, canvasDragDropRequest.getTargetDevice(), widgetEle, false);
            if (canvasDragDropRequest.isAiGenerated()) {
                widgetEle.attr(CANVAS_EDIT_ATTR, "false");
                placeWidgetInPage(canvasDragDropRequest, widgetEle);
            } else {
                placeWidgetInPage(canvasDragDropRequest, pageEle);
            }
            css = getWidgetCssFromCssName(widgetDoc.getString("cssName"));
        } else if (canvasDragDropRequest.isFunctional()) {
            logger.info("Adding functional widget {}", widgetEle.toString());
            String widgetId = widgetEle.attr(CANVAS_FUNC_WIDGET_ATTR);
            Document targetedJobsQuery = new Document(WIDGET_ID_FIELD, widgetId);
            targetedJobsQuery.append("contentType", "targetedJobs");
            if (mongoManager.checkIfDocumentExists("canvas_targetedJobWidgets", db, targetedJobsQuery)) {
                widgetEle.attr("data-widget-type", "phw-targeted-jobs");
            }
            canvasDragDropRequest.setStructure(widgetEle.toString());
            placeWidgetInPage(canvasDragDropRequest, widgetEle);
        }
        JsonNode functionalData = appendChildInstanceIdWithParent(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), widgetEle);
        if (functionalData != null) {
            response.put("functionalData", functionalData);
        }
        response.put(STATUS_KEY, true);
        if (css != null) {
            response.put("css", css);
        }
        if (widgetEle.hasAttr(CANVAS_FUNC_WIDGET_ATTR)) {
            String functionlWidgetId = widgetEle.attr(CANVAS_FUNC_WIDGET_ATTR);
            String contentType = getWidgetIdVsContentType(functionlWidgetId);
            if (functionlWidgetId != null && contentType != null) {
                widgetEle.attr("content-model-type", contentType);
            }
        }
        response.put("processedHtml", widgetEle.toString());
        String pageId = canvasDragDropRequest.getPageId();
        if (!pageId.startsWith(BLOGARTICLE_IDENTIFIER) && !pageId.startsWith(SCREEN_PAGE_IDENTIFIER)) {
            siteUtil.canvasApplyToLowerEnvByEndpoint(Json.fromJson(Json.toJson(canvasDragDropRequest), Map.class), "widget/add", request);
        }
        return response;
    }

    public String getWidgetIdVsContentType(String widgetId) {
        try {
            Document queryDoc = new Document();
            queryDoc.put(WIDGET_ID_FIELD, widgetId);
            return mongoManager.findDocument("canvas_widgetIdVsContentType", conf.getString(MONGO_DB), queryDoc).getString("contentType");
        } catch (Exception e) {
            logger.error("exception with getWidgetIdVsContentType {} {}", e, widgetId);
        }
        return null;
    }

    public JsonNode appendChildInstanceIdWithParent(String refNum, String locale, Element element, String siteVariant, String device, String pageId) {
        String parentInstanceId = element.attr(INSTANCE_ID_FIELD);
        List<String> widgetIds = new ArrayList<>();
        List<String> instanceIds = new ArrayList<>();
        if (element.hasAttr(CANVAS_FUNC_WIDGET_ATTR)) {
            widgetIds.add(element.attr(CANVAS_FUNC_WIDGET_ATTR));
        } else {
            Elements children = element.getElementsByAttribute(CANVAS_FUNC_WIDGET_ATTR);
            for (Element child : children) {
                if (child.hasAttr(INSTANCE_ID_FIELD)) {
                    String finalInstanceId;
                    String[] actualFuncIdSplit = child.attr(INSTANCE_ID_FIELD).split("\\$\\$");
                    if (actualFuncIdSplit.length > 1) {
                        finalInstanceId = child.attr(INSTANCE_ID_FIELD).replace(actualFuncIdSplit[0], parentInstanceId);
                    } else {
                        finalInstanceId = parentInstanceId + "$$" + child.attr(INSTANCE_ID_FIELD);
                    }
                    child.attr(INSTANCE_ID_FIELD, finalInstanceId);
                    widgetIds.add(child.attr(CANVAS_FUNC_WIDGET_ATTR));
                    instanceIds.add(child.attr(INSTANCE_ID_FIELD));
                }
            }
        }
        if (widgetIds.size() == 0) {
            return null;
        }
        return getFunctionalWidgetData(refNum, locale, widgetIds, siteVariant, pageId, device, instanceIds);
    }

    public JsonNode appendChildInstanceIdWithParent(String refNum, String locale, Element element) {
        return appendChildInstanceIdWithParent(refNum, locale, element, null, null, null);
    }

    public JsonNode getFunctionalWidgetData(String refNum, String locale, List<String> widgetIds, String siteVariant, String pageId, String device, List<String> instanceIds) {
        Map<String, Object> payload = new HashMap<>();
        payload.put(REFNUM, refNum);
        payload.put(LOCALE, locale);
        payload.put("widgetIds", widgetIds);
        if (pageId != null) {
            payload.put("instanceIds", instanceIds);
            payload.put(PAGE_ID, pageId);
            payload.put("siteVariant", siteVariant);
            payload.put("device", device);
        }
        try {
            Response responseFromReq = siteUtil.sendPostAsync(conf.getString("get.functional.widget.url"), payload).get();
            if (responseFromReq.getStatus().equals(SUCCESS)) {
                JsonNode contentRes = (JsonNode) responseFromReq.getData();
                if (contentRes.get("status").asText().equals(SUCCESS)) {
                    return contentRes.get("data");
                }
            }
        } catch (Exception ex) {
            logger.error("Error in getFunctionalWidgetData {}", ex);
        }
        return null;
    }

    public String getSavedWidgetHtml(String refNum, String widgetId) {
        try {
            org.bson.Document queryDoc = new org.bson.Document(REFNUM, refNum).append(WIDGET_ID_FIELD, widgetId).append("isAureliaMigrated", true);
            return mongoManager.findDocument("canvas_savedwidgets", conf.getString(MONGO_DB), queryDoc).getString("viewHtml");
        } catch (Exception ex) {
            logger.error("Error in getSavedWidgetHtml {}", ex);
        }
        return null;
    }

    public Map<String, Object> addSavedWidget(CanvasDragDropRequest canvasDragDropRequest, Http.Request request) {
        try {
            Map<String, Object> response = new HashMap<>();
            logger.info("Adding saved widget");
            String savedStructure = canvasDragDropRequest.getStructure();
            Element savedWidgetEle = widgetUtil.getCanvasWidget(savedStructure);
            String instanceId;
            if (!savedWidgetEle.hasAttr(INSTANCE_ID_FIELD)) {
                instanceId = SiteUtil.generateUniqueId();
                savedWidgetEle.attr(INSTANCE_ID_FIELD, instanceId);
            } else {
                instanceId = savedWidgetEle.attr(INSTANCE_ID_FIELD);
            }
            boolean isStatic = false;
            String widgetId = savedWidgetEle.attr(CANVAS_FUNC_WIDGET_ATTR);
            if (savedWidgetEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
                isStatic = true;
                widgetId = savedWidgetEle.attr(CANVAS_STATIC_WIDGET_ATTR);
            }
            String uniqueId = widgetId;
            Document savQuery = new Document(REFNUM, canvasDragDropRequest.getRefNum()).append(WIDGET_ID_FIELD, widgetId);
            org.bson.Document widgetDoc = mongoManager.findDocument(CANVAS_SAVED_WIDGETS, db, savQuery);
            if (widgetDoc == null) {
                widgetDoc = mongoManager.findDocument(CANVAS_DELETED_WIDGETS, db, savQuery);
                if (widgetDoc == null) {
                    response.put(STATUS_KEY, false);
                    response.put("message", "widget doc not found for" + widgetId);
                    return response;
                }
            }
            TenantDetails td = siteUtil.getTenantDetails(canvasDragDropRequest.getRefNum());
            canvasDragDropRequest.setStructure(savedWidgetEle.toString());
            widgetId = widgetDoc.getString("parent_widget_id");
            if (isStatic) {
                savedWidgetEle.attr(CANVAS_STATIC_WIDGET_ATTR, widgetId);
                savedWidgetEle.attr(VIEW_REVNO, "1");
            } else {
                savedWidgetEle.attr(CANVAS_FUNC_WIDGET_ATTR, widgetId);
            }
            if (canvasDragDropRequest.getIsAureliaMigrated()) {
                savedWidgetEle.attr(MIGRATED_WIDGET_IDENTIFIER, "true");
                savedWidgetEle.attr("saved-widget", "true");
            }
            Element pageEle = savedWidgetEle.clone();
            savedWidgetEle.attr("saved-widget-id", uniqueId);
            pageEle.attr("saved-widget-id", uniqueId);
            boolean hasFunctionalWidget = false;
            if (isStatic) {
                //                String viewHtml = widgetDoc.getString("viewHtml");
                //                savedWidgetEle.html(viewHtml);
                if (widgetDoc.containsKey(TAG_CONTENT) && widgetDoc.get(TAG_CONTENT) != null) {
                    List<Document> contentDocs = widgetDoc.get(TAG_CONTENT, List.class);
                    contentDocs.forEach(contentDoc -> {
                        contentDoc.put(LOCALE, canvasDragDropRequest.getLocale());
                        contentDoc.put(PERSONA, canvasDragDropRequest.getSiteVariant());
                        contentDoc.put(PAGE_ID, canvasDragDropRequest.getPageId());
                        contentDoc.put(INSTANCE_ID_FIELD, instanceId);
                        if (td.isMigratedSite()) {
                            contentDoc = createContentForSavedWidget(contentDoc);
                        }
                    });
                    if (contentDocs.size() > 0) {
                        mongoManager.insertDocuments(contentDocs, CANVAS_SITE_CONTENT, db);
                    }
                }
                if (widgetDoc.containsKey("settings") && widgetDoc.get("settings") != null) {
                    generateSettingsMetadata(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getPageId(), instanceId, canvasDragDropRequest.getTargetDevice(), (Map<String, Object>) widgetDoc.get("settings", Map.class));
                }
                if (widgetDoc.containsKey("caasContent") && widgetDoc.get("caasContent") != null) {
                    generateCaasContentData(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getPageId(), instanceId, widgetDoc);
                }
                if (widgetDoc.containsKey("innerWidgets") && (boolean) widgetDoc.get("innerWidgets")) {
                    hasFunctionalWidget = true;
                    addSavedWidgetFunctional(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getPageId(), instanceId, canvasDragDropRequest.getTargetDevice(), uniqueId);
                }
                String processedHtml = "";
                if (canvasDragDropRequest.getIsAureliaMigrated()) {
                    processedHtml = getSavedWidgetHtml(canvasDragDropRequest.getRefNum(), uniqueId);
                    pageEle = savedWidgetEle.html(processedHtml);
                } else {
                    processedHtml = getCanvasRenderedHtml(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getTargetDevice(), canvasDragDropRequest.getPageId(), instanceId, canvasDragDropRequest.getIsAureliaMigrated(), pageEle.toString());
                    savedWidgetEle.html(processedHtml);
                }
            } else {
                hasFunctionalWidget = true;
                addSavedWidgetFunctional(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getPageId(), instanceId, canvasDragDropRequest.getTargetDevice(), uniqueId);
            }
            placeWidgetInPage(canvasDragDropRequest, pageEle);
            String css = getWidgetCssFromCssName(widgetDoc.getString("cssName"));
            if (hasFunctionalWidget) {
                JsonNode functionalData = appendChildInstanceIdWithParent(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), savedWidgetEle, canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getTargetDevice(), canvasDragDropRequest.getPageId());
                if (functionalData != null) {
                    response.put("functionalData", functionalData);
                }
            }
            response.put(STATUS_KEY, true);
            response.put("processedHtml", savedWidgetEle.toString());
            if (css != null) {
                response.put("css", css);
            }
            String pageId = canvasDragDropRequest.getPageId();
            if (!pageId.startsWith(BLOGARTICLE_IDENTIFIER)) {
                siteUtil.canvasApplyToLowerEnvByEndpoint(Json.fromJson(Json.toJson(canvasDragDropRequest), Map.class), "widget/add", request);
            }
            return response;
        } catch (Exception ex) {
            logger.error("Exception in addSavedWidget {}", ex);
            return null;
        }
    }

    public String getWidgetCssFromCssNameWrapper(String cssName) {
        return getWidgetCssFromCssName(cssName);
    }

    private String getWidgetCssFromCssName(String cssName) {
        org.bson.Document doc = preProdMongoManager.findDocument("canvas_css", preprodDb, new Document("cssName", cssName).append("latest", true));
        if (doc != null && doc.containsKey(CSS)) {
            return doc.getString(CSS);
        }
        return null;
    }

    public void generateCaasContentData(String refNum, String locale, String siteVariant, String pageId, String instanceId, Document widgetDoc) {
        List<Document> caasContents = widgetDoc.get("caasContent", List.class);
        String defaultLocale = siteUtil.getTenantDefaultLocale(refNum);
        for (Document caasContent : caasContents) {
            caasContent.put(LOCALE, locale);
            caasContent.put(SITE_VARIANT, siteVariant);
            caasContent.put(INSTANCE_ID_FIELD, instanceId);
            caasContent.put(PAGE_ID, pageId);
            Map<String, Object> query = caasContent.get("query", Map.class);
            if (caasContent.getString("filterType").equals("static")) {
                if (query.containsKey("selectedItems")) {
                    List<String> selectedItems = (List<String>) query.get("selectedItems");
                    List<String> newSelectedItems = new ArrayList<>();
                    selectedItems.forEach(contentId -> {
                        String cloneContentId = cloneCaasContentId(refNum, defaultLocale, locale, contentId, false);
                        if (cloneContentId != null) {
                            newSelectedItems.add(cloneContentId);
                        }
                    });
                    query.put("selectedItems", newSelectedItems);
                }
            }
            String filterId = cloneCaasFilterId(refNum, locale, "filter copy " + instanceId, caasContent.getString("contentType"), caasContent.getString("filterType"), query);
            caasContent.put("filterId", filterId);
            caasContent.put("query", query);
        }
        mongoManager.insertDocuments(caasContents, CANVAS_CAAS_SITE_CONTENT, db);
    }

    private String cloneCaasFilterId(String refNum, String locale, String displayName, String contentType, String filterType, Map<String, Object> query) {
        try {
            Config contentAPIConfig = conf.getConfig("content.api");
            String base = contentAPIConfig.getString("base");
            String url = base + contentAPIConfig.getString("createFilter");
            if (url == null) {
                throw new PhenomException("cloneCaasFilterId.url is not configured in conf file..");
            }
            Map<String, Object> payLoad = new HashMap<>();
            payLoad.put(REFNUM, refNum);
            payLoad.put(LOCALE, locale);
            payLoad.put("displayName", displayName);
            payLoad.put("modelType", contentType);
            payLoad.put("filterType", filterType);
            payLoad.put("query", query);
            payLoad.put("maxCardsToDisplay", 4);
            JsonNode response = remoteServiceCaller.sendPostSync(url, payLoad);
            logger.info(" Response from Content service is : {}", response);
            if (response != null && response.has("status") && (response.get("status").asText()).equalsIgnoreCase(SUCCESS)) {
                return response.get("data").asText();
            } else {
                logger.error(" Error from Content service : {}", response);
                return null;
            }
        } catch (Exception ex) {
            return null;
        }
    }

    public Map<String, Object> addGlobalWidget(CanvasDragDropRequest canvasDragDropRequest, Http.Request request) {
        logger.info("Adding addGlobalWidget widget");
        Map<String, Object> response = new HashMap<>();
        try {
            String structure = canvasDragDropRequest.getStructure();
            Element widgetEle = widgetUtil.getCanvasWidget(structure);
            widgetEle.attr("global-widget", "true");
            widgetEle.attr("global-widget-id", canvasDragDropRequest.getGlobalWidgetId());
            String instanceId = getInstanceIdFromGlobalWidgetId(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getGlobalWidgetId());
            if (instanceId == null) {
                response.put(STATUS_KEY, false);
                response.put("message", "Widget not found in panel!");
                return response;
            }
            widgetEle.attr(INSTANCE_ID_FIELD, instanceId);
            logger.info("Adding global widget {}", widgetEle.toString());
            String widgetId = widgetEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR) ? widgetEle.attr(CANVAS_STATIC_WIDGET_ATTR) : widgetEle.attr(CANVAS_FUNC_WIDGET_ATTR);
            org.bson.Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
            if (widgetDoc == null) {
                widgetDoc = mongoManager.findDocument(CANVAS_SITEWIDGETS, db, new Document(WIDGET_ID_FIELD, widgetId).append(REFNUM, canvasDragDropRequest.getRefNum()));
            }
            if (widgetDoc == null && !canvasDragDropRequest.getIsAureliaMigrated()) {
                response.put(STATUS_KEY, false);
                response.put("message", "widget doc not found for" + widgetId);
                return response;
            }
            if (canvasDragDropRequest.getIsAureliaMigrated()) {
                widgetEle.attr(MIGRATED_WIDGET_IDENTIFIER, "true");
                widgetEle.attr(CANVAS_EDIT_ATTR, "true");
            }
            canvasDragDropRequest.setStructure(widgetEle.toString());
            if (widgetEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
                Document viewDoc = null;
                if (!canvasDragDropRequest.getIsAureliaMigrated()) {
                    viewDoc = getCanvasViewDocument(widgetDoc.getString(VIEW_ID));
                }
                if (viewDoc == null && !canvasDragDropRequest.getIsAureliaMigrated()) {
                    String viewId = widgetDoc.getString(VIEW_ID);
                    viewDoc = mongoManager.findDocument(CANVAS_SITEWIDGETVIEWS, db, new Document(REFNUM, canvasDragDropRequest.getRefNum()).append(VIEW_ID, viewId));
                }
                if (viewDoc == null && !canvasDragDropRequest.getIsAureliaMigrated()) {
                    response.put(STATUS_KEY, false);
                    response.put("message", "view doc not found for" + widgetDoc.getString(VIEW_ID));
                    return response;
                }
                String version = null;
                if (!canvasDragDropRequest.getIsAureliaMigrated()) {
                    version = viewDoc.containsKey("importNumber") ? Integer.toString(viewDoc.getInteger("importNumber")) : null;
                    widgetEle.attr(VIEW_REVNO, version);
                }
                canvasDragDropRequest.setStructure(widgetEle.toString());
                String globalPageId = getAnyGlobalWidgetPageId(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getGlobalWidgetId());
                String renderedHtml = getCanvasRenderedHtml(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), canvasDragDropRequest.getSiteVariant(), DESKTOP, globalPageId, instanceId, canvasDragDropRequest.getIsAureliaMigrated(), widgetEle.toString());
                if (renderedHtml != null) {
                    widgetEle.html(renderedHtml);
                }
            }
            String css = getWidgetCssFromCssName(!canvasDragDropRequest.getIsAureliaMigrated() ? widgetDoc.getString("cssName") : "");
            placeWidgetInPage(canvasDragDropRequest, widgetEle);
            if (canvasDragDropRequest.getIsAureliaMigrated()) {
                copyGlobalWidgetLocaleContent(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), instanceId);
            }
            addGlobalWidgetMetadata(canvasDragDropRequest, instanceId, widgetEle.attr(DATA_ATTRIBUTE_ID), widgetId);
            response.put(STATUS_KEY, true);
            if (css != null) {
                response.put("css", css);
            }
            JsonNode functionalData = appendChildInstanceIdWithParent(canvasDragDropRequest.getRefNum(), canvasDragDropRequest.getLocale(), widgetEle, canvasDragDropRequest.getSiteVariant(), canvasDragDropRequest.getTargetDevice(), canvasDragDropRequest.getPageId());
            if (functionalData != null) {
                response.put("functionalData", functionalData);
            }
            response.put("processedHtml", widgetEle.toString());
            String pageId = canvasDragDropRequest.getPageId();
            if (!pageId.startsWith(BLOGARTICLE_IDENTIFIER)) {
                siteUtil.canvasApplyToLowerEnvByEndpoint(Json.fromJson(Json.toJson(canvasDragDropRequest), Map.class), "widget/addGlobalWidget", request);
            }
        } catch (Exception ex) {
            response.put(STATUS_KEY, false);
            logger.error("Exception in addGlobalWidget {}", ex);
        }
        return response;
    }

    public String getAnyGlobalWidgetPageId(String refNum, String locale, String globalWidgetId) {
        Document result = mongoManager.findDocument(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, new Document(REFNUM, refNum).append(LOCALE, locale).append("globalWidgetId", globalWidgetId));
        if (result != null) {
            return result.getString(PAGE_ID);
        }
        return null;
    }

    public Map<String, Object> disconnectGlobalWidget(String refNum, String locale, String siteVariant, String pageId, String instanceId, String targetId, String type, String newInstanceId) {
        Map<String, Object> response = new HashMap<>();
        if (newInstanceId == null) {
            newInstanceId = SiteUtil.generateUniqueId();
        }
        response.put("newInstanceId", newInstanceId);
        logger.info("disconnect static global widget {}", instanceId);
        Document query = new Document(REFNUM, refNum).append(LOCALE, locale).append(INSTANCE_ID_FIELD, instanceId).append(GLOBAL_WIDGET, true);
        List<Document> contentDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CONTENT, db, query);
        String finalNewInstanceId = newInstanceId;
        contentDocs.forEach(doc -> {
            doc.remove(ID);
            doc.remove(GLOBAL_WIDGET);
            doc.put(LOCALE, locale);
            doc.put(PERSONA, siteVariant);
            doc.append(PAGE_ID, pageId);
            doc.put(INSTANCE_ID_FIELD, finalNewInstanceId);
        });
        if (!contentDocs.isEmpty()) {
            mongoManager.insertDocuments(contentDocs, CANVAS_SITE_CONTENT, db);
        }
        query.remove(LOCALE);
        query.remove(INSTANCE_ID_FIELD);
        query.put("$or", getInnerWidgetsQuery(INSTANCE_ID_FIELD, instanceId));
        List<Document> settingsDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_INSTANCE_SETTINGS, db, query);
        settingsDocs.forEach(doc -> {
            doc.remove(ID);
            doc.remove(GLOBAL_WIDGET);
            doc.put(LOCALE, locale);
            doc.put(SITE_VARIANT, siteVariant);
            doc.append(PAGE_ID, pageId);
            String innerInstanceId = doc.getString(INSTANCE_ID_FIELD);
            String finalInstanceId = innerInstanceId.replace(innerInstanceId.split("\\$\\$")[0], finalNewInstanceId);
            doc.put(INSTANCE_ID_FIELD, finalInstanceId);
        });
        if (!settingsDocs.isEmpty()) {
            mongoManager.insertDocuments(settingsDocs, CANVAS_SITE_INSTANCE_SETTINGS, db);
        }
        List<Document> settingsMetaDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db, query);
        settingsMetaDocs.forEach(doc -> {
            doc.remove(ID);
            doc.remove(GLOBAL_WIDGET);
            doc.put(LOCALE, locale);
            doc.put(SITE_VARIANT, siteVariant);
            doc.append(PAGE_ID, pageId);
            String innerInstanceId = doc.getString(INSTANCE_ID_FIELD);
            String finalInstanceId = innerInstanceId.replace(innerInstanceId.split("\\$\\$")[0], finalNewInstanceId);
            doc.put(INSTANCE_ID_FIELD, finalInstanceId);
        });
        if (!settingsMetaDocs.isEmpty()) {
            mongoManager.insertDocuments(settingsMetaDocs, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
        }
        generateCaasContentForDisconnect(refNum, locale, siteVariant, pageId, instanceId, newInstanceId);
        disconnectGlobalFuncWidget(refNum, locale, siteVariant, pageId, instanceId, newInstanceId);
        if (targetId != null) {
            //            query.append("targetId", targetId);
            query.remove("globalWidget");
            mongoManager.deleteDocument(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, query);
            updateInstanceIdInPage(refNum, locale, siteVariant, pageId, newInstanceId, targetId);
        }
        response.put(STATUS_KEY, true);
        return response;
    }

    public void generateCaasContentForDisconnect(String refNum, String locale, String siteVariant, String pageId, String instanceId, String newInstanceId) {
        String defaultLocale = siteUtil.getTenantDefaultLocale(refNum);
        Document siteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(INSTANCE_ID_FIELD, instanceId).append(GLOBAL_WIDGET, true);
        List<Document> caasContents = mongoManager.findAllDocuments(CANVAS_CAAS_SITE_CONTENT, db, siteQuery);
        for (Document caasContent : caasContents) {
            caasContent.remove("_id");
            caasContent.remove(GLOBAL_WIDGET);
            caasContent.put(LOCALE, locale);
            caasContent.put(SITE_VARIANT, siteVariant);
            caasContent.put(INSTANCE_ID_FIELD, newInstanceId);
            caasContent.put(PAGE_ID, pageId);
            Map<String, Object> query = caasContent.get("query", Map.class);
            if (caasContent.getString("filterType").equals("static")) {
                if (query.containsKey("selectedItems")) {
                    List<String> selectedItems = (List<String>) query.get("selectedItems");
                    List<String> newSelectedItems = new ArrayList<>();
                    selectedItems.forEach(contentId -> {
                        String cloneContentId = cloneCaasContentId(refNum, defaultLocale, locale, contentId, false);
                        if (cloneContentId != null) {
                            newSelectedItems.add(cloneContentId);
                        }
                    });
                    query.put("selectedItems", newSelectedItems);
                }
            }
            String filterId = cloneCaasFilterId(refNum, locale, "filter copy " + newInstanceId, caasContent.getString("contentType"), caasContent.getString("filterType"), query);
            caasContent.put("filterId", filterId);
            caasContent.put("query", query);
        }
        if (caasContents.size() > 0) {
            mongoManager.insertDocuments(caasContents, CANVAS_CAAS_SITE_CONTENT, db);
        }
    }

    public void updateInstanceIdInPage(String refNum, String locale, String siteVariant, String pageId, String newInstanceId, String targetId) {
        List<String> devices = Arrays.asList("desktop", "mobile");
        for (String device : devices) {
            String pageKey = siteUtil.constructPhPageKey(refNum, device, locale, pageId);
            String pageVal = redisManager.get(pageKey);
            if (pageVal != null) {
                Page pageObj = Json.fromJson(Json.parse(pageVal), Page.class);
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageObj.getPageHtml());
                Element widget = pageDoc.body().getElementsByAttributeValue(DATA_ATTRIBUTE_ID, targetId).first();
                if (widget != null) {
                    widget.attr(INSTANCE_ID_FIELD, newInstanceId);
                    widget.attr(CANVAS_EDIT_ATTR, "true");
                    widget.removeAttr("global-widget");
                    widget.removeAttr("global-widget-id");
                }
                pageObj.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(pageObj).toString());
            }
        }
    }

    public String getInstanceIdFromGlobalWidgetId(String refNum, String globalWidgetId) {
        Document query = new Document(REFNUM, refNum).append("globalWidgetId", globalWidgetId);
        Document doc = mongoManager.findDocument(CANVAS_SITE_GLOBAL_WIDGET_PANEL, db, query);
        if (doc != null) {
            return doc.getString(INSTANCE_ID_FIELD);
        }
        doc = mongoManager.findDocument(CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db, query);
        if (doc != null) {
            return doc.getString(INSTANCE_ID_FIELD);
        }
        return null;
    }

    public void addGlobalWidgetMetadata(CanvasDragDropRequest canvasDragDropRequest, String instanceId, String targetId, String widgetId) {
        Document insertQuery = new Document(REFNUM, canvasDragDropRequest.getRefNum()).append(LOCALE, canvasDragDropRequest.getLocale()).append(SITE_VARIANT, canvasDragDropRequest.getSiteVariant());
        insertQuery.append(WIDGET_ID_FIELD, widgetId);
        insertQuery.append(INSTANCE_ID_FIELD, instanceId);
        insertQuery.append("globalWidgetId", canvasDragDropRequest.getGlobalWidgetId());
        insertQuery.append(PAGE_ID, canvasDragDropRequest.getPageId());
        insertQuery.append("targetId", targetId);
        mongoManager.insertDocument(insertQuery, CANVAS_SITE_GLOBAL_WIDGET_METADATA, db);
    }

    public Map<String, Object> renameGlobalWidget(String refNum, String globalWidgetId, String displayName, JsonNode payload, Http.Request request) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        org.bson.Document query = new org.bson.Document(REFNUM, refNum);
        query.append(DATA.GLOBAL_WIDGET_ID, globalWidgetId);
        if (mongoManager.checkIfDocumentExists(CANVAS_SITE_GLOBAL_WIDGET_PANEL, db, query)) {
            mongoManager.upsert(query, new org.bson.Document("$set", new org.bson.Document("name", displayName)), CANVAS_SITE_GLOBAL_WIDGET_PANEL, db);
        } else if (mongoManager.checkIfDocumentExists("canvas_migrated_aurelia_globalWidgets", db, query)) {
            mongoManager.upsert(query, new org.bson.Document("$set", new org.bson.Document("name", displayName)), "canvas_migrated_aurelia_globalWidgets", db);
        } else {
            response.put(STATUS_KEY, false);
            response.put("message", "Global Widget not found");
        }
        return response;
    }

    public Map<String, Object> renameSavedWidget(String refNum, String savedWidgetId, String displayName) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        org.bson.Document query = new org.bson.Document(REFNUM, refNum);
        query.append(DATA.WIDGET_ID_FIELD, savedWidgetId);
        if (mongoManager.checkIfDocumentExists(CANVAS_SAVED_WIDGETS, db, query)) {
            mongoManager.upsert(query, new org.bson.Document("$set", new org.bson.Document("name", displayName)), CANVAS_SAVED_WIDGETS, db);
        } else {
            response.put(STATUS_KEY, false);
            response.put("message", "Saved Widget not found");
        }
        return response;
    }

    public Map<String, Object> deleteGlobalWidget(String refNum, String globalWidgetId) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        org.bson.Document query = new org.bson.Document(REFNUM, refNum);
        query.append(DATA.GLOBAL_WIDGET_ID, globalWidgetId);
        TenantDetails td = siteUtil.getTenantDetails(refNum);
        if (!mongoManager.checkIfDocumentExists(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, query)) {
            //soft deleting only from panel instead of content & styles
            mongoManager.deleteDocument(CANVAS_SITE_GLOBAL_WIDGET_PANEL, db, query);
            if (td != null && td.isMigratedSite()) {
                mongoManager.deleteDocument("canvas_migrated_aurelia_globalWidgets", db, query);
            }
        } else {
            response.put(STATUS_KEY, false);
            response.put("message", "Global Widget is used in some pages, cannot be deleted");
        }
        return response;
    }

    public Map<String, Object> copyGlobalWidget(String refNum, String globalWidgetId, String displayName, String uniqueId, String newInstanceId, String conversionInstanceId) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        org.bson.Document query = new org.bson.Document(REFNUM, refNum);
        query.append(DATA.GLOBAL_WIDGET_ID, globalWidgetId);
        Document globalDoc = mongoManager.findDocument(CANVAS_SITE_GLOBAL_WIDGET_PANEL, db, query);
        if (globalDoc == null && isCanvasMigratedSite(refNum, Optional.empty())) {
            globalDoc = mongoManager.findDocument(Constants.CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db, query);
            if (globalDoc != null) {
                globalDoc.put(WIDGET_ID_FIELD, null);
                globalDoc.put(SITE_VARIANT, globalDoc.getString(PERSONA));
            }
        }
        if (globalDoc != null && globalDoc.size() > 0) {
            String locale = globalDoc.getString(LOCALE);
            String siteVariant = globalDoc.getString(SITE_VARIANT);
            String instanceId = globalDoc.getString(INSTANCE_ID_FIELD);
            String widgetId = globalDoc.getString(WIDGET_ID_FIELD);
            Map<String, Object> disconnectResponse = disconnectGlobalWidget(refNum, locale, siteVariant, "copyId", instanceId, null, "Static", newInstanceId);
            newInstanceId = disconnectResponse.get("newInstanceId").toString();
            response.put("newInstanceId", newInstanceId);
            Map<String, Object> data = convertToGlobalWidget(refNum, locale, siteVariant, "copyId", newInstanceId, widgetId, null, displayName, uniqueId, conversionInstanceId, globalWidgetId);
            if (data.containsKey("conversionInstanceId")) {
                response.put("conversionInstanceId", data.get("newInstanceId").toString());
            }
            if (data.containsKey("uniqueId")) {
                response.put("uniqueId", data.get("uniqueId"));
            }
        } else {
            response.put(STATUS_KEY, false);
            response.put("message", "Global Widget not found");
        }
        return response;
    }

    private void generateSettingsMetadata(String refNum, String locale, String siteVariant, String pageId, String instanceId, String targetDevice, Map<String, Object> settings) {
        List<String> styleIds = new ArrayList<>();
        Set<String> styleIdsSet = new HashSet<>();
        widgetUtil.extractStyleIds(settings, styleIdsSet);
        Document settingDoc = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append("device", DESKTOP).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId).append("settings", settings).append(STYLE_IDS, styleIdsSet);
        mongoManager.insertDocument(settingDoc, CANVAS_SITE_INSTANCE_SETTINGS, db);
        settings.forEach((key, value) -> {
            if (key.equals("cards")) {
                if (value != null) {
                    List<Map<String, Object>> cardSettings = (List<Map<String, Object>>) settings.get("cards");
                    cardSettings.forEach(data -> {
                        data.forEach((cardKey, cardValue) -> {
                            if (cardValue != null && cardValue.toString().length() > 0) {
                                styleIds.add(cardValue.toString());
                            }
                        });
                    });
                }
            } else if (value != null && value.toString().length() > 0) {
                styleIds.add(value.toString());
            }
        });
        List<Document> insertDocs = new ArrayList<>();
        styleIds.forEach(styleId -> {
            Document doc = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append("device", targetDevice).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId).append(STYLE_ID, styleId);
            insertDocs.add(doc);
        });
        mongoManager.insertDocuments(insertDocs, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
    }

    public org.bson.Document getCanvasViewDocument(String viewId) {
        org.bson.Document queryDoc = new org.bson.Document(VIEW_ID, viewId);
        queryDoc.put(LATEST, true);
        return preProdMongoManager.findDocument(Constants.CANVAS_GLOBALWIDGETVIEWS, preprodDb, queryDoc);
    }

    public void placeWidgetInPage(CanvasDragDropRequest canvasDragDropRequest, Element pageElement) {
        if (!pageElement.hasAttr("has-edit")) {
            pageElement.attr("has-edit", "true");
        }
        if (canvasDragDropRequest.isAutomationScreen()) {
            return;
        }
        //        List<String> devices =
        //                canvasDragDropRequest.getTargetDevice().equalsIgnoreCase(MOBILE) ? Arrays.asList(MOBILE) :
        //                        Arrays.asList(DESKTOP, MOBILE);
        List<String> devices = Arrays.asList(DESKTOP, MOBILE);
        for (String device : devices) {
            String pageKey = SiteUtil.constructPhPageKey(canvasDragDropRequest.getRefNum(), device, canvasDragDropRequest.getLocale(), canvasDragDropRequest.getPageId());
            String pageContent = redisManager.get(pageKey);
            Page page = Json.fromJson(Json.parse(pageContent), Page.class);
            org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
            if (pageElement.classNames().contains("phw-d-none")) {
                pageElement.removeClass("phw-d-none");
            }
            if (canvasDragDropRequest.getTargetDevice().equalsIgnoreCase(MOBILE) && device.equalsIgnoreCase(DESKTOP)) {
                pageElement.addClass("phw-d-none");
            }
            String structure = pageElement.toString();
            if (canvasDragDropRequest.getNextSiblingId() != null) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getNextSiblingId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.before(structure);
                }
            } else if (canvasDragDropRequest.getPreviousSiblingId() != null) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getPreviousSiblingId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.after(structure);
                }
            } else if (canvasDragDropRequest.getParentElementId() != null && canvasDragDropRequest.isAddLast()) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getParentElementId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.append(structure);
                }
            } else if (canvasDragDropRequest.getParentElementId() != null) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getParentElementId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.prepend(structure);
                }
            } else {
                logger.error("Unknown place to add new value as NextSiblingId, PreviousSiblingId and ParentElementId are nulls for canavas widget addition");
            }
            page.setPageHtml(pageDoc.toString());
            redisManager.set(pageKey, Json.toJson(page).toString());
        }
    }

    public Map<String, Object> getContent(String refNum, String locale, String siteVariant, String pageId, String instanceId) {
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        query.append(SITE_VARIANT, siteVariant);
        query.append(PAGE_ID, pageId);
        query.append(INSTANCE_ID_FIELD, instanceId);
        Document contentDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CONTENT_MAPPINGS, db, query);
        if (contentDoc != null && contentDoc.containsKey("contentMap")) {
            return (Map<String, Object>) contentDoc.get("contentMap");
        }
        return null;
    }

    public Map<String, Object> processAndGetFlattenedContent(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device, String widgetId, boolean globalWidget, boolean aureliaWidget) {
        Map<String, Object> response = new HashMap<>();
        Map<String, List<Map<String, Object>>> content = new HashMap<>();
        Map<String, String> linkTextMap = getLinkTextMetadata(refNum, widgetId);
        Map<String, String> dataPsVsTag = new HashMap<>();
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        if (!globalWidget) {
            query.append(PERSONA, siteVariant);
            query.append(PAGE_ID, pageId);
        } else {
            query.append(GLOBAL_WIDGET, true);
        }
        query.append(INSTANCE_ID_FIELD, instanceId);
        query.append(DATA.DEVICE, DESKTOP);
        try {
            query.append(DATA_PS, new Document("$nin", linkTextMap.values()));
            List<Document> contentDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CONTENT, db, query);
            query.append(DATA_PS, new Document("$in", linkTextMap.values()));
            List<Document> linktextDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CONTENT, db, query);
            processCaasContent(contentDocs, refNum, locale, device);
            int maxCardIndex = -1;
            for (Document contentDoc : contentDocs) {
                if (contentDoc.getString(CONTAINER_TYPE).equalsIgnoreCase(CARD)) {
                    if (contentDoc.getInteger(INDEX) > maxCardIndex) {
                        maxCardIndex = contentDoc.getInteger(INDEX);
                    }
                }
            }
            List<Map<String, Object>> cardsList = new ArrayList<>();
            for (int i = maxCardIndex; i >= 0; i--) {
                cardsList.add(new HashMap<>());
            }
            content.put("cards", cardsList);
            if (!contentDocs.isEmpty()) {
                for (Document contentDoc : contentDocs) {
                    try {
                        contentDoc.remove(ID);
                        String dataPs = contentDoc.getString(DATA_PS);
                        dataPsVsTag.put(dataPs, contentDoc.getString(NODE));
                        if (contentDoc.getString(CONTAINER_TYPE).equalsIgnoreCase(WIDGET)) {
                            content.putIfAbsent(dataPs, new ArrayList<>());
                            content.get(dataPs).add(contentDoc);
                        } else {
                            Map<String, Object> cardData = content.get("cards").get(contentDoc.getInteger(INDEX));
                            cardData.putIfAbsent(dataPs, new ArrayList<>());
                            List<Map<String, Object>> data = (List<Map<String, Object>>) cardData.get(dataPs);
                            data.add(contentDoc);
                            cardData.put(dataPs, data);
                            content.get("cards").set(contentDoc.getInteger(INDEX), cardData);
                        }
                    } catch (Exception e) {
                        logger.error("Exception while processing for {}", contentDoc, e);
                    }
                }
                handleLinkTextDocs(content, linktextDocs, linkTextMap);
            }
        } catch (Exception e) {
            logger.error("Exception while getting content for {}", query, e);
        }
        handleDeviceSpecificContent(content, device);
        response.put(TAG_CONTENT, content);
        response.put("dataPsVsTag", dataPsVsTag);
        response.put("metaData", getViewMetaData(refNum, widgetId));
        response.put("caasContentTypes", getCaasContentTypes(widgetId));
        if (aureliaWidget) {
            response.put("viewHtml", fetchWidgetViewHtmlFromPage(refNum, locale, siteVariant, pageId, instanceId, DESKTOP, globalWidget));
            response.put("mobileViewHtml", fetchWidgetViewHtmlFromPage(refNum, locale, siteVariant, pageId, instanceId, MOBILE, globalWidget));
        }
        return response;
    }

    public List<String> getCaasContentTypes(String widgetId) {
        Document query = new Document(WIDGET_ID_FIELD, widgetId);
        query.append(LATEST, true);
        Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, query);
        if (widgetDoc == null) {
            logger.error("Could not find widget doc for {}", widgetId);
            return new ArrayList<>();
        }
        Document viewQuery = new Document(LATEST, true);
        viewQuery.append(VIEW_ID, widgetDoc.getString(VIEW_ID));
        Document viewDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETVIEWS, preprodDb, viewQuery);
        if (viewDoc != null) {
            String viewHtml = viewDoc.getString("viewHtml");
            org.jsoup.nodes.Document htmlDoc = HtmlParser.parse(viewHtml);
            Elements repeatables = htmlDoc.getElementsByAttribute(CANVAS_REPEATABLE);
            if (!repeatables.isEmpty()) {
                Element repeatable = repeatables.first();
                List<String> dataMapIds = repeatable.getElementsByAttribute(DATA_MAP_ID).stream().map(ele -> ele.attr(DATA_MAP_ID)).collect(Collectors.toList());
                if (!dataMapIds.isEmpty()) {
                    return getMatchingCaasModels(dataMapIds);
                }
            }
        }
        return new ArrayList<>();
    }

    public List<String> getMatchingCaasModels(List<String> dataMapIds) {
        List<String> types = new ArrayList<>();
        int matchingCount = conf.hasPath("matchingCount") ? conf.getInt("matchingCount") : 2;
        Map<String, List<String>> typeVsMapIds = new HashMap<>();
        List<Document> mappingDocs = preProdMongoManager.findAllDocuments(CANVAS_CAAS_CONTENT_GLOBAL_MAPPING, preprodDb, new Document(), Arrays.asList(DATA_MAP_ID, CONTENT_TYPE));
        for (Document mappingDoc : mappingDocs) {
            String type = mappingDoc.getString(CONTENT_TYPE);
            typeVsMapIds.putIfAbsent(type, new ArrayList<>());
            typeVsMapIds.get(type).add(mappingDoc.getString(DATA_MAP_ID));
        }
        for (String type : typeVsMapIds.keySet()) {
            if (getCommonElementsCount(typeVsMapIds.get(type), dataMapIds) >= matchingCount) {
                types.add(type);
            }
        }
        return types;
    }

    public int getCommonElementsCount(List<String> typeIds, List<String> widgetMapIds) {
        Set<String> common = new HashSet<>(typeIds);
        common.retainAll(widgetMapIds);
        return common.size();
    }

    public void handleLinkTextDocs(Map<String, List<Map<String, Object>>> content, List<Document> linkTextDocs, Map<String, String> linkTextMap) {
        for (String dataPs : content.keySet()) {
            if (linkTextMap.keySet().contains(dataPs)) {
                Document linkTextDoc = getLinkTextDoc(WIDGET, 0, linkTextMap.get(dataPs), linkTextDocs);
                if (linkTextDoc != null) {
                    content.get(dataPs).add(linkTextDoc);
                }
            } else if (dataPs.equalsIgnoreCase("cards")) {
                List<Map<String, Object>> cardsData = content.get("cards");
                int index = 0;
                for (Map<String, Object> eachCard : cardsData) {
                    for (String cardPs : eachCard.keySet()) {
                        if (linkTextMap.keySet().contains(cardPs)) {
                            Document linkTextDoc = getLinkTextDoc(CARD, index, linkTextMap.get(cardPs), linkTextDocs);
                            if (linkTextDoc != null) {
                                List<Map<String, Object>> dataPsData = (List<Map<String, Object>>) eachCard.get(cardPs);
                                dataPsData.add(linkTextDoc);
                            }
                        }
                    }
                    index++;
                }
            }
        }
    }

    public Document getLinkTextDoc(String containerType, int index, String textDataPs, List<Document> linkTextDocs) {
        for (Document linkTextDoc : linkTextDocs) {
            linkTextDoc.remove(ID);
            if (linkTextDoc.getString(CONTAINER_TYPE).equalsIgnoreCase(containerType) && linkTextDoc.getInteger(INDEX).equals(index) && linkTextDoc.getString(DATA_PS).equalsIgnoreCase(textDataPs)) {
                return linkTextDoc;
            }
        }
        return null;
    }

    public void processCaasContent(List<Document> contentDocs, String refNum, String locale, String device) {
        Set<String> contentIds = new HashSet<>();
        for (Document contentDoc : contentDocs) {
            String content = getDeviceSpecificContent(contentDoc, device);
            if (canHaveCaasContent(contentDoc)) {
                contentIds.add(content);
            }
        }
        if (!contentIds.isEmpty()) {
            Map<String, Object> payload = new HashMap<>();
            payload.put(REFNUM, refNum);
            payload.put(LOCALE, locale);
            payload.put("contentIds", contentIds);
            JsonNode response = contentService.getContentServiceApiResponse("getBulkContent", payload);
            if (response != null && response.has("status") && (response.get("status").asText()).equalsIgnoreCase(SUCCESS)) {
                logger.info(" ##### Successfully received contents data from content service ###");
                List<Map<String, Object>> contentsData = Json.fromJson(response.get("data"), List.class);
                Map<String, Object> processedData = processContents(contentsData);
                for (Document contentDoc : contentDocs) {
                    String content = getDeviceSpecificContent(contentDoc, device);
                    if (content != null && processedData.containsKey(content)) {
                        contentDoc.put(content, processedData.get(content));
                    }
                }
            } else {
                logger.error(" ##### Could not get content data from content service ###");
            }
        }
    }

    public Map<String, Object> processContents(List<Map<String, Object>> contentsData) {
        Map<String, Object> contentsMap = new HashMap<>();
        for (Map<String, Object> content : contentsData) {
            if (content != null && content.containsKey(CONTENT_ID)) {
                contentsMap.put((String) content.get(CONTENT_ID), content);
            }
        }
        return contentsMap;
    }

    public void flatContentAndUpdate(String refNum, String locale, String siteVariant, String pageId, String instanceId, Map<String, Object> contentMap, String device, boolean globalWidget, String hfType, boolean aureliaWidget) {
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        if (!globalWidget) {
            query.append(PERSONA, siteVariant);
            query.append(PAGE_ID, pageId);
        } else {
            query.append(GLOBAL_WIDGET, true);
        }
        query.append(INSTANCE_ID_FIELD, instanceId);
        List<Document> contentDocs = new ArrayList<>();
        for (String key : contentMap.keySet()) {
            if (!key.equalsIgnoreCase("cards")) {
                List<Map<String, Object>> tagContent = (List<Map<String, Object>>) contentMap.get(key);
                for (Map<String, Object> attrData1 : tagContent) {
                    String content = (String) attrData1.get(TAG_CONTENT);
                    if (attrData1.containsKey("edited")) {
                        attrData1.remove("edited");
                        if (!device.equalsIgnoreCase(DESKTOP)) {
                            String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
                            String desktopContent = (String) attrData1.get(DESKTOP_CONTENT);
                            attrData1.put(deviceSpecificContentKey, content);
                            attrData1.put(TAG_CONTENT, desktopContent);
                            attrData1.remove(DESKTOP_CONTENT);
                        }
                    } else {
                        if (!device.equalsIgnoreCase(DESKTOP)) {
                            String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
                            if (attrData1.containsKey(deviceSpecificContentKey)) {
                                String desktopContent = (String) attrData1.get(DESKTOP_CONTENT);
                                attrData1.put(TAG_CONTENT, desktopContent);
                            }
                            attrData1.remove(DESKTOP_CONTENT);
                        }
                    }
                    contentDocs.add(new Document(attrData1));
                }
            } else {
                List<Map<String, Object>> cardsData = (List<Map<String, Object>>) contentMap.get(key);
                int index = -1;
                for (Map<String, Object> card : cardsData) {
                    index++;
                    for (String cardPs : card.keySet()) {
                        List<Map<String, Object>> tagData = (List<Map<String, Object>>) card.get(cardPs);
                        for (Map<String, Object> attrData : tagData) {
                            String content = (String) attrData.get(TAG_CONTENT);
                            if (attrData.containsKey("edited")) {
                                attrData.remove("edited");
                                if (!device.equalsIgnoreCase(DESKTOP)) {
                                    String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
                                    String desktopContent = (String) attrData.get(DESKTOP_CONTENT);
                                    attrData.put(deviceSpecificContentKey, content);
                                    attrData.put(TAG_CONTENT, desktopContent);
                                    attrData.remove(DESKTOP_CONTENT);
                                }
                            } else {
                                if (!device.equalsIgnoreCase(DESKTOP)) {
                                    String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
                                    if (attrData.containsKey(deviceSpecificContentKey)) {
                                        String desktopContent = (String) attrData.get(DESKTOP_CONTENT);
                                        attrData.put(TAG_CONTENT, desktopContent);
                                    }
                                    attrData.remove(DESKTOP_CONTENT);
                                }
                            }
                            attrData.put(INDEX, index);
                            contentDocs.add(new Document(attrData));
                        }
                    }
                }
            }
        }
        if (!contentDocs.isEmpty()) {
            filterAndRemoveDuplicates(refNum, locale, pageId, instanceId, globalWidget, contentDocs);
            mongoManager.deleteDocuments(query, Constants.CANVAS_SITE_CONTENT, db);
            mongoManager.insertDocuments(contentDocs, Constants.CANVAS_SITE_CONTENT, db);
            //            mongoManager.bulkUpsert(contentDocsNew, Constants.CANVAS_SITE_CONTENT, db);
            addOrRemoveDeviceOverriddenAttr(refNum, locale, pageId, instanceId, device, "add");
            makeWidgetInstanceDirty(refNum, locale, pageId, instanceId);
        }
        if (instanceId.startsWith("hf-")) {
            setPageHFHasEditTrue(refNum, locale, instanceId);
            updateHFPublishStatus(refNum, locale, instanceId);
        }
        if (globalWidget) {
            setGlobalWidgetHasEditTrue(refNum, locale, instanceId);
        }
        if (aureliaWidget && hfType != null) {
            updateHasEditForNonCaasHeaderFooter(refNum, locale, pageId, hfType);
        }
    }

    public void updateHasEditForNonCaasHeaderFooter(String refNum, String locale, String pageId, String type) {
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        query.append(PAGE_ID, pageId);
        query.append(TYPE, type);
        Document assignDoc = mongoManager.findDocument(SITE_NON_CAAS_HF_PAGE_ASSIGNMENTS, conf.getString(MONGO_DB), query);
        if (assignDoc != null) {
            String hfInstanceId = assignDoc.getString("hfInstanceId");
            org.bson.Document queryDocument = new org.bson.Document();
            queryDocument.put(REFNUM, refNum);
            queryDocument.put("locale", locale);
            queryDocument.put("type", type);
            queryDocument.put("viewName", hfInstanceId);
            List<Document> hfDocs = mongoManager.findAllDocuments(SITE_HEADER_FOOTERS, conf.getString(MONGO_DB), queryDocument);
            for (Document hfDoc : hfDocs) {
                hfDoc.remove(ID);
                String html = hfDoc.getString("viewHtml");
                Element ele = Jsoup.parse(html).body().child(0);
                if (ele.tagName().equalsIgnoreCase("section")) {
                    ele.attr(CANVAS_EDIT_ATTR, "true");
                    queryDocument.put("deviceType", hfDoc.getString("deviceType"));
                    hfDoc.put("viewHtml", ele.toString());
                    mongoManager.upsert(queryDocument, new Document("$set", hfDoc), SITE_HEADER_FOOTERS, conf.getString(MONGO_DB));
                }
            }
        }
    }

    public void filterAndRemoveDuplicates(String refNum, String locale, String pageId, String instanceId, boolean globalWidget, List<Document> contentDocs) {
        try {
            Document baseQuery = new Document(REFNUM, refNum);
            baseQuery.append(LOCALE, locale);
            baseQuery.append(INSTANCE_ID, instanceId);
            baseQuery.append(DEVICE_TYPE, DESKTOP);
            if (!globalWidget) {
                baseQuery.append(PAGE_ID, pageId);
            } else {
                baseQuery.append("globalWidget", true);
            }
            Set<String> duplicates = new HashSet<>();
            updateInstanceData(contentDocs, duplicates);
            Document query = new Document(baseQuery);
            query.append("operation", "updateContent");
            mongoManager.deleteDocument("canvas_duplicate_content_log", conf.getString(MONGO_DB), query);
            if (!duplicates.isEmpty()) {
                logger.info("Found duplicates for canvas static content for {}", query);
                query.append("duplicateIds", duplicates);
                mongoManager.insertDocument(query, "canvas_duplicate_content_log", conf.getString(MONGO_DB));
            }
            if (conf.hasPath("content.removeDuplicates") && conf.getBoolean("content.removeDuplicates")) {
                for (String duplicate : duplicates) {
                    String[] keys = duplicate.split("%%");
                    String containerType = keys[1];
                    int index = Integer.parseInt(keys[2]);
                    String dataPs = keys[3];
                    String contentKey = keys[4];
                    int count = 0;
                    Iterator<Document> iterator = contentDocs.iterator();
                    while (iterator.hasNext()) {
                        Document contentDoc = iterator.next();
                        if (contentDoc.getString(CONTAINER_TYPE).equalsIgnoreCase(containerType) && contentDoc.getInteger(INDEX).equals(index) && contentDoc.getString(DATA_PS).equalsIgnoreCase(dataPs) && contentDoc.getString(CONTENT_KEY).equalsIgnoreCase(contentKey)) {
                            if (count > 0) {
                                iterator.remove();
                            }
                            count++;
                        }
                    }
                }
            }
        } catch (Exception e) {
            logger.error("Exception in filterAndRemoveDuplicates", e);
        }
    }

    public void updateInstanceData(List<Document> contentDocs, Set<String> duplicateIds) {
        Set<String> uniqueIds = new HashSet<>();
        for (Document contentDoc : contentDocs) {
            String uniqueHash = contentDoc.getString("instance-id") + "%%" + contentDoc.getString("containerType") + "%%" + contentDoc.getInteger("index") + "%%" + contentDoc.getString("data-ps") + "%%" + contentDoc.getString("contentKey");
            if (uniqueIds.contains(uniqueHash)) {
                duplicateIds.add(uniqueHash);
                continue;
            }
            uniqueIds.add(uniqueHash);
        }
    }

    public void setPageHFHasEditTrue(String refNum, String locale, String instanceId) {
        org.bson.Document query = new org.bson.Document(REFNUM, refNum);
        query.append(DATA.LOCALE, locale);
        query.append("hfInstanceId", instanceId);
        mongoManager.updateMany(query, new org.bson.Document(HAS_EDIT, new org.bson.Document()), Constants.CANVAS_SITE_HF_PAGE_ASSIGNMENTS, db);
    }

    public void setGlobalWidgetHasEditTrue(String refNum, String locale, String instanceId) {
        org.bson.Document query = new org.bson.Document(REFNUM, refNum);
        if (locale != null) {
            query.append(DATA.LOCALE, locale);
        }
        query.append(INSTANCE_ID_FIELD, instanceId);
        mongoManager.updateMany(query, new org.bson.Document(HAS_EDIT, new org.bson.Document()), Constants.CANVAS_SITE_GLOBAL_WIDGET_METADATA, db);
        query.remove(LOCALE);
        org.bson.Document globalWidgetDoc = mongoManager.findDocument(CANVAS_SITE_GLOBALWIDGET_PANEL, conf.getString(MONGO_DB), query);
        boolean migratedWidget = false;
        if (isCanvasMigratedSite(refNum, Optional.empty()) && globalWidgetDoc == null) {
            globalWidgetDoc = mongoManager.findDocument("canvas_migrated_aurelia_globalWidgets", conf.getString(MONGO_DB), query);
            migratedWidget = globalWidgetDoc != null;
        }
        String publishedState = DRAFT;
        if (globalWidgetDoc != null && globalWidgetDoc.containsKey(PUBLISHEDSTATE) && !globalWidgetDoc.getString(PUBLISHEDSTATE).equals(DRAFT)) {
            publishedState = UNPUBLISHED;
        } else if (migratedWidget && globalWidgetDoc.get(PUBLISHEDSTATE) == null) {
            publishedState = UNPUBLISHED;
        }
        Document updateDoc = new Document(PUBLISHEDSTATE, publishedState);
        try {
            String collection = migratedWidget ? "canvas_migrated_aurelia_globalWidgets" : CANVAS_SITE_GLOBALWIDGET_PANEL;
            mongoManager.upsertWithoutReplace(globalWidgetDoc, new Document("$set", updateDoc), collection, conf.getString(MONGO_DB));
        } catch (Exception e) {
            logger.error("couldn't update canvas global widget state --> {} ", e);
        }
    }

    public JsonNode getWidgetStyles(String refNum, String locale, String siteVariant, String pageId, String instanceId, boolean globalWidget, boolean isHF) {
        ObjectNode resp = Json.newObject();
        Document query = getSettingsQuery(refNum, locale, siteVariant, pageId, instanceId, globalWidget, isHF);
        Document settingDoc = mongoManager.findDocument(Constants.CANVAS_SITE_INSTANCE_SETTINGS, db, query);
        if (settingDoc != null) {
            Set<String> styleIds = new HashSet<>();
            Map<String, Object> data = (Map<String, Object>) settingDoc.get("settings");
            resp.set("metaData", Json.toJson(data));
            //            for (String dataPs : data.keySet()) {
            //                if (dataPs.equalsIgnoreCase("cards")) {
            //                    List<Map<String, Object>> cardsData = (List<Map<String, Object>>) data.get("cards");
            //                    for (Map<String, Object> card : cardsData) {
            //                        card.keySet().forEach(cardPs -> {
            //                            styleIds.add((String) card.get(cardPs));
            //                        });
            //                    }
            //                } else {
            //                    styleIds.add((String) data.get(dataPs));
            //                }
            //            }
            //            resp.set("styleData", getSettingsJsonFromStyleIds(refNum, styleIds));
            return resp;
        }
        return resp;
    }

    public Document updateWidgetStyles(String refNum, String locale, String siteVariant, String pageId, String instanceId, Map<String, Object> data, boolean globalWidget, boolean isHF, String widgetType, String device, Map<String, Object> childInstanceVsDataPs) {
        Document activityResp = new Document();
        Document query = getSettingsQuery(refNum, locale, siteVariant, pageId, instanceId, globalWidget, isHF);
        Document settingDoc = new Document(query);
        settingDoc.append("settings", data);
        Set<String> styleIdsSet = new HashSet<>();
        widgetUtil.extractStyleIds(data, styleIdsSet);
        settingDoc.append(STYLE_IDS, styleIdsSet);
        Document oldSettings = mongoManager.findDocument(CANVAS_SITE_INSTANCE_SETTINGS, db, query);
        if (oldSettings != null && oldSettings.get("settings") != null) {
            activityResp.put("previousValue", oldSettings.get("settings"));
            activityResp.put("newValue", data);
        }
        mongoManager.upsert(query, new Document("$set", settingDoc), CANVAS_SITE_INSTANCE_SETTINGS, db);
        updateChildInstanceSettings(childInstanceVsDataPs, data, query);
        if (widgetType.equalsIgnoreCase("functional")) {
            updateMetaDataInCaasDb(CANVAS_SITE_INSTANCE_SETTINGS, query, settingDoc);
        }
        addOrRemoveDeviceOverriddenAttr(refNum, locale, pageId, instanceId, device, "add");
        makeWidgetInstanceDirty(refNum, locale, pageId, instanceId);
        generateWidgetStyleMetadata(refNum, locale, siteVariant, pageId, instanceId, null, data, globalWidget, isHF);
        if (instanceId.startsWith("hf-")) {
            setPageHFHasEditTrue(refNum, locale, instanceId);
            updateHFPublishStatus(refNum, locale, instanceId);
        }
        if (globalWidget) {
            setGlobalWidgetHasEditTrue(refNum, null, instanceId);
        }
        return activityResp;
    }

    public static Document getSettingsQuery(String refNum, String locale, String siteVariant, String pageId, String instanceId, boolean globalWidget, boolean isHF) {
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        query.append(SITE_VARIANT, siteVariant);
        query.append(PAGE_ID, pageId);
        if (globalWidget) {
            query.remove(LOCALE);
            query.remove(SITE_VARIANT);
            query.remove(PAGE_ID);
            query.append(GLOBAL_WIDGET, true);
        } else if (isHF) {
            query.remove(SITE_VARIANT);
            query.remove(PAGE_ID);
            query.append(GLOBAL_WIDGET, true);
        }
        query.append(DEVICE_TYPE, DESKTOP);
        query.append(INSTANCE_ID_FIELD, instanceId);
        return query;
    }

    public void updateMetaDataInCaasDb(String collection, Document query, Document settingDoc) {
        String url = conf.getString("template.updateData.url");
        if (url == null) {
            logger.error("Template Service Endpoint is not configured in conf");
            return;
        }
        Map<String, Object> params = new HashMap<>();
        params.put("collection", collection);
        params.put("query", query);
        params.put("updateDoc", settingDoc);
        remoteServiceCaller.sendPostASync(url, params);
    }

    public Document updateStyle(String refNum, String tag, String styleId, Map<String, Object> data, String displayName, boolean textStyle, String state, String deviceType, String type, Map<String, Object> visibilitySettings, String theme, Map<String, Object> overlaySettings, boolean copyOldGlobalStyleData, String oldStyleId, boolean savedStyle, boolean fromDetach, String cardBlockStyleId, Map<String, Object> cardBlockChilds, String fontPresetStyleId, boolean generateCssWithId, boolean fromBuildStyles) {
        if (copyOldGlobalStyleData && oldStyleId != null) {
            copyOldGlobalStylesDataForOtherDevices(refNum, theme, tag, styleId, deviceType, displayName, oldStyleId, fromDetach, generateCssWithId, fromBuildStyles);
            handleCardHoverData(refNum, theme, tag, styleId, deviceType, displayName, oldStyleId, fromDetach, cardBlockStyleId, cardBlockChilds, generateCssWithId, fromBuildStyles);
        }
        org.bson.Document defaultSettingsDoc = mongoManager.findDocument("canvas_tag_default_settings", db, new Document());
        List<String> buttonStyleIds = (List<String>) defaultSettingsDoc.get("defaultSavedStyleIds", List.class);
        if (buttonStyleIds.contains(styleId)) {
            savedStyle = true;
        }
        Document query = new Document(REFNUM, refNum);
        Document activityResp = new Document();
        query.append(TAG, tag);
        query.append(DEVICE_TYPE, deviceType);
        query.append(STYLE_ID, styleId);
        query.append(THEME, theme);
        if (state != null) {
            query.append("state", state);
            if (state.equalsIgnoreCase("cardHover")) {
                query.append("cardBlockStyleId", cardBlockStyleId);
            }
        } else {
            query.append("state", new Document("$exists", false));
        }
        Document styleDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        if (styleDoc == null) {
            activityResp.put("previousValue", new Document());
            if (state != null) {
                query.append("state", state);
            } else {
                query.remove("state");
            }
            styleDoc = new Document(query);
        } else {
            activityResp.put("previousValue", new Document(styleDoc));
        }
        styleDoc.remove(ID);
        if (isCanvasMigratedSite(refNum, Optional.empty()) && (!fromBuildStyles || (fromBuildStyles && styleId.startsWith("phw-g-i-")))) {
            styleDoc.append(EDITED_STYLES, getChangedCssProperties(refNum, tag, deviceType, (null == oldStyleId ? styleId : oldStyleId), theme, state, cardBlockStyleId, data));
        }
        Map<String, Object> editedStyleData = new HashMap<>();
        //TODO Handle card hover style generation
        String css = "";
        if (!refNum.equalsIgnoreCase(CANVAS_REFNUM)) {
            css = constructCss(data, styleId, tag, state, deviceType, visibilitySettings, overlaySettings, cardBlockStyleId, refNum, generateCssWithId, theme, oldStyleId, fromBuildStyles);
        }
        if (!deviceType.equalsIgnoreCase("desktop") && data.containsKey("act-p-font-size")) {
            data.remove("act-p-font-size");
        }
        styleDoc.append("styles", data);
        styleDoc.append("css", css);
        if (textStyle) {
            styleDoc.append("textStyle", textStyle);
        }
        if (savedStyle) {
            styleDoc.append("savedStyle", savedStyle);
        }
        //        if(styleId.equalsIgnoreCase("default")){
        //            styleDoc.put("savedStyle", true);
        //            savedStyle=true;
        //        }
        if (state != null) {
            query.append("state", state);
        } else {
            query.append("state", new Document("$exists", false));
        }
        //        if (type != null) {
        //            query.append("type", type);
        //        }
        if (displayName != null) {
            styleDoc.append(DISPLAY_NAME, displayName);
        }
        if (visibilitySettings != null && visibilitySettings.size() > 0) {
            styleDoc.append(VISIBILITY_SETTINGS, visibilitySettings);
        }
        if (!overlaySettings.isEmpty() && tag.equalsIgnoreCase(PHW_IMG_CTR)) {
            styleDoc.append(OVERLAY_SETTINGS, overlaySettings);
        }
        //        if(oldStyleId != null && styleId.startsWith("phw-g-i")){
        //            styleDoc.put("type", "custom");
        //        }else if(!styleDoc.containsKey("type") && mongoManager.checkIfDocumentExists(CANVAS_SITE_CUSTOM_STYLES,db, new Document(REFNUM, CANVUS_REFNUM).append(TAG, tag).append(STYLE_ID, styleId))){
        //            styleDoc.put("type", "system");
        //        }
        styleDoc.append(THEME, theme);
        if (fromDetach) {
            styleDoc.remove("savedStyle");
        }
        mongoManager.upsert(query, new Document("$set", styleDoc), CANVAS_SITE_CUSTOM_STYLES, db);
        //TODO: handle cardhover states in font size calculation for other devices
        if (!refNum.equalsIgnoreCase(CANVUS_REFNUM) && deviceType.equalsIgnoreCase("desktop") && data.containsKey("font-size")) {
            addCalculatedValuesToDevices(refNum, theme, tag, styleId, state, MOBILE, data.get("font-size").toString(), "font-size", displayName, savedStyle, fromDetach, cardBlockStyleId, generateCssWithId, fromBuildStyles);
            addCalculatedValuesToDevices(refNum, theme, tag, styleId, state, "tab", data.get("font-size").toString(), "font-size", displayName, savedStyle, fromDetach, cardBlockStyleId, generateCssWithId, fromBuildStyles);
        }
        if (oldStyleId != null && oldStyleId.startsWith("phw-g-i-")) {
            //            addOldStyleIdInCanvasStylesQueue(refNum, oldStyleId);
            scheduleDeleteStyle(refNum, oldStyleId, tag, theme);
        }
        //TODO: Should not send/use query into below method as we are using the same to upsert the documents
        //If any changes to the query made in future, might break styleDocs/create duplicates/corrupt the data
        if (state != null || !deviceType.equalsIgnoreCase(DESKTOP)) {
            addIfNormalStateDocIsMissing(new Document(query), displayName, savedStyle, fromDetach, generateCssWithId, fromBuildStyles);
        }
        // adding system/custom type to stylescdnUrl
        Document systemQuery = new Document(REFNUM, refNum);
        systemQuery.put(THEME, theme);
        systemQuery.put(TAG, tag);
        systemQuery.put(STYLE_ID, styleId);
        //TODO: Verify this part
        type = "custom";
        if (mongoManager.checkIfDocumentExists(CANVAS_SITE_CUSTOM_STYLES, db, new Document(REFNUM, CANVUS_REFNUM).append(TAG, tag).append(STYLE_ID, styleId))) {
            type = "system";
        } else if (styleDoc.containsKey(TYPE)) {
            type = styleDoc.getString(TYPE);
        } else if (!styleDoc.containsKey(TYPE)) {
            systemQuery.put(TYPE, new Document("$exists", true));
            Document docWithType = mongoManager.findDocument(CANVAS_SITE_CUSTOM_STYLES, db, systemQuery);
            if (docWithType != null) {
                type = docWithType.getString(TYPE);
            } else {
                logger.info("docwithtype is null --> {}", systemQuery);
            }
        }
        systemQuery.remove(TYPE);
        if (fromDetach) {
            type = "custom";
            mongoManager.unsetFields(systemQuery, new Document("$unset", new Document("savedStyle", 1)), CANVAS_SITE_CUSTOM_STYLES, db);
        }
        Document updateDoc = new Document("type", type);
        if (!fromDetach && isSavedStyle(refNum, theme, styleId)) {
            updateDoc.put("savedStyle", true);
        }
        if (styleId.equalsIgnoreCase(DEFAULT)) {
            updateDoc.put(TYPE, "system");
        }
        mongoManager.updateMany(systemQuery, updateDoc, CANVAS_SITE_CUSTOM_STYLES, db);
        updatePCMChangesForOtherDevicesIfEditedInDesktop(refNum, theme, tag, styleId, state, deviceType, generateCssWithId, fromBuildStyles);
        if (styleId.startsWith("phw-f-")) {
            CompletableFuture.runAsync(() -> {
                updateFontPresetCSSInAllParents(refNum, theme, styleId, generateCssWithId, fromBuildStyles);
            });
        }
        Document fontPresetQuery = new Document(REFNUM, refNum);
        fontPresetQuery.append(THEME, theme);
        fontPresetQuery.append(STYLE_ID, fontPresetStyleId);
        if (fontPresetStyleId != null) {
            List<Document> docs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, fontPresetQuery);
            List<String> fontPrestDevices = mongoManager.getUniqueList(DEVICE_TYPE, fontPresetQuery, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
            if (docs.size() > 0) {
                updateFontPresetValuesInSingleStyle(refNum, theme, tag, styleId, docs, fontPrestDevices, false, generateCssWithId, fromBuildStyles);
            }
        } else {
            fontPresetQuery.append(STYLE_ID, styleId);
            mongoManager.unsetFields(fontPresetQuery, new Document("$unset", new Document("fontPresetId", 1)), CANVAS_SITE_CUSTOM_STYLES, db);
        }
        String cssUrl = "";
        if (!refNum.equalsIgnoreCase(CANVAS_REFNUM)) {
            cssUrl = addCustomStylesCssToPage(refNum, theme);
        }
        updateThemePublishStatus(refNum, theme);
        timer.schedule(new TimerTask() {

            @Override
            public void run() {
                logger.info("widget style/attribute usage data generation started at {}", new Date());
                logger.info("for refNum {}, styleId: {}, state: {}, device: {}, data: {}", refNum, styleId, state, deviceType, data);
                generateStyleAttributesMetadata(refNum, styleId, state, deviceType, data, theme);
            }
        }, 3000);
        logger.info("scheduled attribute usage data generation task for updateStyle!");
        styleDoc.put("cssUrl", cssUrl);
        activityResp.put("newValue", styleDoc);
        return activityResp;
    }

    public void updateFontPresetValuesInSingleStyle(String refNum, String theme, String tag, String styleId, List<Document> fontPresetDocs, List<String> actualfontPrestDevices, boolean generateCss, boolean generateCssWithId, boolean fromBuildStyles) {
        List<String> fontPrestDevices = actualfontPrestDevices != null ? new ArrayList<>(actualfontPrestDevices) : new ArrayList<>();
        Document query = new Document(REFNUM, refNum);
        query.put(THEME, theme);
        query.put(TAG, tag);
        query.put(STYLE_ID, styleId);
        List<Document> docs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        List<String> styleDevices = mongoManager.getUniqueList(DEVICE_TYPE, query, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        if (styleDevices != null && !styleDevices.isEmpty()) {
            fontPrestDevices.removeAll(styleDevices);
        }
        if (fontPrestDevices != null && !fontPrestDevices.isEmpty()) {
            docs.addAll(createEmptyDocsForFontPreset(refNum, theme, tag, styleId, fontPrestDevices));
        }
        for (Document eachDoc : docs) {
            Map<String, Object> styles = (Map<String, Object>) eachDoc.get("styles");
            List<String> keysToRemove = Arrays.asList("font-size", "font-family", "font-weight", "line-height", "letter-spacing");
            for (String key : keysToRemove) {
                styles.remove(key);
            }
            Map<String, Object> nonFontStyles = new HashMap<>(styles);
            if (!eachDoc.containsKey("state")) {
                fontPresetDocs.forEach(eachFontPresetDoc -> {
                    if (eachDoc.getString(DEVICE_TYPE).equalsIgnoreCase(eachFontPresetDoc.getString(DEVICE_TYPE))) {
                        styles.putAll((Map<? extends String, ?>) eachFontPresetDoc.get("styles"));
                    }
                });
            }
            String css = constructCss(styles, styleId, tag, eachDoc.getString("state"), eachDoc.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), eachDoc.getString("cardBlockStyleId"), refNum, generateCssWithId, theme, null, fromBuildStyles);
            eachDoc.put(CSS, css);
            eachDoc.put("styles", nonFontStyles);
            eachDoc.remove(ID);
            eachDoc.put("fontPresetId", fontPresetDocs.get(0).getString(STYLE_ID));
        }
        mongoManager.deleteDocuments(query, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        mongoManager.insertDocuments(docs, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        if (generateCss) {
            addCustomStylesCssToPage(refNum, theme);
        }
    }

    public List<Document> createEmptyDocsForFontPreset(String refNum, String theme, String tag, String styleId, List<String> fontPrestDevices) {
        List<Document> emptyDocs = new ArrayList<>();
        for (String device : fontPrestDevices) {
            Document emptyDoc = new Document(REFNUM, refNum);
            emptyDoc.put(THEME, theme);
            emptyDoc.put(TAG, tag);
            emptyDoc.put(STYLE_ID, styleId);
            emptyDoc.put(DEVICE_TYPE, device);
            emptyDoc.put("styles", new HashMap<>());
            emptyDocs.add(emptyDoc);
        }
        mongoManager.insertDocuments(emptyDocs, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        return emptyDocs;
    }

    public void updateFontPresetCSSInAllParents(String refNum, String theme, String fontPresetId, boolean generateCssWithId, boolean fromBuildStyles) {
        Document query = new Document(REFNUM, refNum);
        query.put(THEME, theme);
        query.put(STYLE_ID, fontPresetId);
        List<Document> fontPresetDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        List<String> fontPrestDevices = mongoManager.getUniqueList(DEVICE_TYPE, query, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        Document usageQuery = new Document(REFNUM, refNum);
        usageQuery.put(THEME, theme);
        usageQuery.put("fontPresetId", fontPresetId);
        List<String> projections = Arrays.asList(STYLE_ID, "tag");
        Map<String, String> styleIdvsTag = new HashMap<>();
        List<Document> usageDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, usageQuery, projections);
        usageDocs.forEach(eachUsageDoc -> {
            if (eachUsageDoc.getString(STYLE_ID) != null && eachUsageDoc.getString(TAG) != null) {
                styleIdvsTag.put(eachUsageDoc.getString(STYLE_ID), eachUsageDoc.getString(TAG));
            }
        });
        styleIdvsTag.keySet().forEach(styleId -> {
            updateFontPresetValuesInSingleStyle(refNum, theme, styleIdvsTag.get(styleId), styleId, fontPresetDocs, fontPrestDevices, false, generateCssWithId, fromBuildStyles);
        });
        addCustomStylesCssToPage(refNum, theme);
    }

    public void addOldStyleIdInCanvasStylesQueue(String refNum, String oldStyleId) {
        Document canvasStyleDoc = new Document(REFNUM, refNum);
        Document resultDoc = mongoManager.findDocument(CANVAS_STYLES_QUEUE, db, canvasStyleDoc);
        Set<String> styleIds = new HashSet<>();
        if (resultDoc != null) {
            styleIds = new HashSet<String>(resultDoc.get("styleIds", List.class));
        }
        styleIds.add(oldStyleId);
        Document updateDoc = new Document(canvasStyleDoc);
        updateDoc.put("styleIds", styleIds);
        mongoManager.upsert(canvasStyleDoc, new Document("$set", updateDoc), CANVAS_STYLES_QUEUE, db);
    }

    public void scheduleDeleteStyle(String refNum, String oldStyleId, String tag, String theme) {
        List<String> allowedRefNums = Arrays.asList();
        if (allowedRefNums.contains(refNum) || allowedRefNums.contains("default")) {
            timer.schedule(new TimerTask() {

                @Override
                public void run() {
                    logger.info("scheduleDeleteStyle started at {} for refNum: {}, style: {}, tag: {}, theme: {}", new Date(), refNum, oldStyleId, tag, theme);
                    deleteStyle(refNum, tag, oldStyleId, theme);
                }
            }, 10000);
        }
    }

    /*    public Document updateStyleOld(String refNum, String tag, String styleId, Map<String, Object> data,
                                   String displayName, boolean textStyle, String state, String deviceType, String type,
                                   Map<String, Object> visibilitySettings, String theme,
                                   Map<String, Object> overlaySettings) {
        Document query = new Document(REFNUM, refNum);
        Document activityResp = new Document();
        query.append(TAG, tag);
        query.append(DEVICE_TYPE, deviceType);
        query.append(STYLE_ID, styleId);
        query.append(THEME, theme);
        Document styleDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        activityResp.put("previousValue", styleDoc);
        if (styleDoc == null) {
            styleDoc = new Document(query);
        }
        styleDoc.remove(ID);
        String css = constructCss(data, styleId, tag, state, deviceType, visibilitySettings, overlaySettings);
        styleDoc.append("styles", data);
        styleDoc.append("css", css);
        if (textStyle) {
            styleDoc.append("textStyle", textStyle);
        }
        if (state != null) {
            query.append("state", state);
        }

        if (type != null) {
            query.append("type", type);
        }

        if (displayName != null) {
            styleDoc.append(DISPLAY_NAME, displayName);
        }
        styleDoc.append(VISIBILITY_SETTINGS, visibilitySettings);
        activityResp.put("newValue", styleDoc);
        mongoManager.upsert(query, new Document("$set", styleDoc), CANVAS_SITE_CUSTOM_STYLES, db);
        addCustomStylesCssToPage(refNum, theme);
        timer.schedule(new TimerTask() {
            @Override
            public void run() {
                logger.info("widget style/attribute usage data generation started at {}", new Date());
                logger.info("for refNum {}, styleId: {}, state: {}, device: {}, data: {}", refNum, styleId, state,
                        deviceType, data);
                generateStyleAttributesMetadata(refNum, styleId, state, deviceType, data, theme);
            }
        }, 3000);
        logger.info("scheduled attribute usage data generation task for updateStyle!");
        return activityResp;
    }*/
    public void generateStyleAttributesMetadata(String refNum, String styleId, String state, String deviceType, Map<String, Object> data, String theme) {
        generateAttributeMetaData(refNum, styleId, state, deviceType, data, "color", theme);
        generateAttributeMetaData(refNum, styleId, state, deviceType, data, "shadow", theme);
        generateAttributeMetaData(refNum, styleId, state, deviceType, data, "font", theme);
        generateAttributeMetaData(refNum, styleId, state, deviceType, data, "gradients", theme);
        /*        CompletableFuture.runAsync(() -> {
            Map<String, Object> response = getStyleAttributeUsage(refNum, styleId, "style", theme);
            if (response != null && !response.isEmpty() && (int) response.get("count") > 0) {
                logger.info("style to update is: {}", styleId);
                updateAttributeUsage(refNum, styleId, "style", response);
            }
        });*/
    }

    public void generateAttributeMetaData(String refNum, String styleId, String state, String deviceType, Map<String, Object> data, String type, String theme) {
        List<String> attributes;
        if (type.equalsIgnoreCase("color")) {
            attributes = Arrays.asList("color", "border-color", "background-color", "border-bottom-color", "border-left-color", "border-right-color", "border-top-color", "caret-color", "column-rule-color", "outline-color", "text-decoration-color");
        } else if (type.equalsIgnoreCase("shadow")) {
            attributes = Arrays.asList("box-shadow", "text-shadow");
        } else if (type.equalsIgnoreCase("font")) {
            attributes = Arrays.asList("font-family");
        } else if (type.equalsIgnoreCase("gradients")) {
            attributes = Arrays.asList("background", "background-image");
        } else {
            attributes = Collections.emptyList();
        }
        String collection = "canvas_site_type_metadata".replace("type", type);
        Document deleteQuery = new Document(REFNUM, refNum);
        deleteQuery.put(STYLE_ID, styleId);
        if (state != null) {
            deleteQuery.put("state", state);
        } else {
            deleteQuery.put("state", new Document("$exists", false));
        }
        deleteQuery.put(DATA.DEVICE, deviceType);
        //delete existing metadata for attribute
        mongoManager.deleteDocuments(deleteQuery, collection, db);
        Set<String> ids = new HashSet<>();
        data.entrySet().forEach(entry -> {
            if (attributes.contains(entry.getKey())) {
                if (!type.equalsIgnoreCase("gradients")) {
                    ids.add(entry.getValue().toString());
                } else if (entry.getValue().toString().startsWith("var(--")) {
                    ids.add(entry.getValue().toString());
                }
            }
        });
        List<Document> metadataDocs = new ArrayList<>();
        for (String id : ids) {
            Document metaDataDoc = new Document(REFNUM, refNum);
            metaDataDoc.put(STYLE_ID, styleId);
            if (state != null) {
                metaDataDoc.put("state", state);
            }
            metaDataDoc.put(DATA.DEVICE, deviceType);
            metaDataDoc.put("variable_name", id);
            metadataDocs.add(metaDataDoc);
        }
        if (!metadataDocs.isEmpty()) {
            mongoManager.insertDocuments(metadataDocs, collection, db);
            /*            CompletableFuture.runAsync(() -> {
                for (String id : ids) {
                    logger.info("attributes are : {}", ids);
                    Map<String, Object> response = getStyleAttributeUsage(refNum, id, type, theme);
                    if (response != null && !response.isEmpty() && (int) response.get("count") > 0) {
                        logger.info("attribute to update is: {}", id);

                        updateAttributeUsage(refNum, id, type, response);
                    }
                }
            });*/
        }
    }

    public void updateAttributeUsage(String refNum, String name, String type, Map<String, Object> data) {
        logger.info("update attribute usage parameters are {}, {}, {}", refNum, name, type);
        Document query = new Document(REFNUM, refNum).append("variable_name", name).append("type", type);
        Document updateDoc = new Document("usage_data", data);
        logger.info("update doc is : {}", updateDoc);
        mongoManager.upsert(query, new Document("$set", updateDoc), "canvas_site_style_usage_data", db);
    }

    public String getMediaQueryWrapper(String deviceType, org.bson.Document doc) {
        if (doc != null) {
            if (doc.containsKey("mediaQueries") && doc.get("mediaQueries") != null) {
                Map<String, String> mediaQueries = (Map<String, String>) doc.get("mediaQueries");
                return mediaQueries.get(deviceType);
            }
        }
        return null;
    }

    public String constructCss(Map<String, Object> data, String styleId, String tag, String state, String deviceType, Map<String, Object> visibilitySettings, Map<String, Object> overlaySettings, String cardBlockStyleId, String refNum, boolean generateCssWithId, String theme, String oldStyleId, boolean fromBuildStyles) {
        TenantDetails td = siteUtil.getTenantDetails(refNum);
        boolean isMigratedSite = td.isMigratedSite();
        List<String> styleIdsToAddImportant = Arrays.asList("phw-event-description", "phw-blog-description", "phw-job-description");
        final boolean[] addImportantToSpecificStyle = { false };
        styleIdsToAddImportant.forEach(style -> {
            if (styleId.contains(style)) {
                addImportantToSpecificStyle[0] = true;
            }
        });
        StringBuffer br = new StringBuffer();
        org.bson.Document defaultSettingsDoc = mongoManager.findDocument("canvas_tag_default_settings", db, new Document());
        List<String> stylesToRestrict = new ArrayList<>();
        if (defaultSettingsDoc.containsKey("componentStyles") && defaultSettingsDoc.get("componentStyles") != null) {
            Map<String, Object> componentStyles = (Map<String, Object>) defaultSettingsDoc.get("componentStyles");
            visibilitySettings.keySet().forEach(eachComponent -> {
                if (!(Boolean) visibilitySettings.get(eachComponent)) {
                    stylesToRestrict.addAll((List<String>) componentStyles.get(eachComponent));
                }
            });
        }
        Map<String, Object> extraClasses = new HashMap<>();
        Map<String, Object> extraClassesToAdd = new HashMap<>();
        Map<String, String> identifierMappings = new HashMap<>();
        List<String> imgStyles = new ArrayList<>();
        List<String> commonImgStyles = new ArrayList<>();
        List<String> pcmEnabledTags = new ArrayList<>();
        if (!isMigratedSite && defaultSettingsDoc.containsKey("extraClasses") && defaultSettingsDoc.get("extraClasses") != null) {
            extraClasses = (Map<String, Object>) defaultSettingsDoc.get("extraClasses");
        }
        if (isMigratedSite && defaultSettingsDoc.containsKey("extraClassesToAdd") && defaultSettingsDoc.get("extraClassesToAdd") != null) {
            extraClassesToAdd = (Map<String, Object>) defaultSettingsDoc.get("extraClassesToAdd");
        }
        if (defaultSettingsDoc.containsKey("identifierMappings") && defaultSettingsDoc.get("identifierMappings") != null) {
            identifierMappings = (Map<String, String>) defaultSettingsDoc.get("identifierMappings");
        }
        if (defaultSettingsDoc.containsKey(IMG_STYLES) && defaultSettingsDoc.get(IMG_STYLES) != null) {
            imgStyles = (List<String>) defaultSettingsDoc.get(IMG_STYLES);
        }
        if (defaultSettingsDoc.containsKey(COMMON_IMG_STYLES) && defaultSettingsDoc.get(COMMON_IMG_STYLES) != null) {
            commonImgStyles = (List<String>) defaultSettingsDoc.get(COMMON_IMG_STYLES);
        }
        if (defaultSettingsDoc.containsKey(PCM_ENABLED_TAGS) && defaultSettingsDoc.get(PCM_ENABLED_TAGS) != null) {
            pcmEnabledTags = (List<String>) defaultSettingsDoc.get(PCM_ENABLED_TAGS);
        }
        Map<String, String> sliderClassExtension = new HashMap<>();
        if (defaultSettingsDoc.containsKey("sliderClassExtension") && defaultSettingsDoc.get("sliderClassExtension") != null) {
            sliderClassExtension = (Map<String, String>) defaultSettingsDoc.get("sliderClassExtension");
        }
        List<String> buttonStyleIds = (List<String>) defaultSettingsDoc.get("defaultSavedStyleIds", List.class);
        List<String> tagDefaultClasses = Arrays.asList("phw-btn", "phw-widget-ctr", "phw-card-block", "phw-content-block", "phw-icon-ctr", "phw-slider-ctr");
        List<String> inputTypes = Arrays.asList("checkbox", "radio");
        if (defaultSettingsDoc.containsKey("tagDefaultClasses") && defaultSettingsDoc.get("tagDefaultClasses") != null) {
            tagDefaultClasses = (List<String>) defaultSettingsDoc.get("tagDefaultClasses");
        }
        List<String> suffixes = Arrays.asList("");
        if (state != null && !state.equalsIgnoreCase("cardHover")) {
            suffixes = getStateSuffix(tag, state);
        }
        if (tag.equalsIgnoreCase("overlay")) {
            suffixes = Arrays.asList("::before");
        }
        List<String> stylesAscendingOrder = new ArrayList<>();
        if (defaultSettingsDoc.containsKey("stylesAscendingOrder")) {
            stylesAscendingOrder.addAll((List<String>) defaultSettingsDoc.get("stylesAscendingOrder", List.class));
        }
        if (suffixes == null) {
            return "";
        }
        for (String suffix : suffixes) {
            Map<String, Object> changedCssProperties = getChangedCssProperties(refNum, tag, deviceType, (null == oldStyleId ? styleId : oldStyleId), theme, state, cardBlockStyleId, data);
            br = getSuffix(styleId, tag, suffix, data, extraClasses, extraClassesToAdd, identifierMappings, sliderClassExtension, inputTypes, br, isMigratedSite, cardBlockStyleId, state, tagDefaultClasses, pcmEnabledTags, false, fromBuildStyles);
            br = constructInnerCss(stylesAscendingOrder, imgStyles, commonImgStyles, styleId, tag, data, overlaySettings, br, isMigratedSite, addImportantToSpecificStyle, stylesToRestrict, changedCssProperties, false, fromBuildStyles);
            if (!fromBuildStyles && isMigratedSite && pcmEnabledTags.contains(tag) && generateCssWithId) {
                br = getSuffix(styleId, tag, suffix, changedCssProperties, extraClasses, extraClassesToAdd, identifierMappings, sliderClassExtension, inputTypes, br, isMigratedSite, cardBlockStyleId, state, tagDefaultClasses, pcmEnabledTags, generateCssWithId, fromBuildStyles);
                br = constructInnerCss(stylesAscendingOrder, imgStyles, commonImgStyles, styleId, tag, changedCssProperties, overlaySettings, br, isMigratedSite, addImportantToSpecificStyle, stylesToRestrict, changedCssProperties, generateCssWithId, fromBuildStyles);
            }
        }
        if (deviceType != null && !deviceType.equalsIgnoreCase("desktop")) {
            StringBuffer mediaQueryBr = new StringBuffer();
            String mediaQueryWrapper = getMediaQueryWrapper(deviceType, defaultSettingsDoc);
            mediaQueryBr.append(mediaQueryWrapper + " " + "{" + "\n");
            mediaQueryBr.append(br);
            mediaQueryBr.append("}" + "\n");
            return mediaQueryBr.toString();
        } else {
            return br.toString();
        }
    }

    public List<String> getStateSuffix(String tag, String state) {
        org.bson.Document doc = mongoManager.findDocument("canvas_tag_default_settings", db, new Document());
        if (doc != null) {
            if (doc.containsKey("designSettings") && doc.get("designSettings") != null) {
                Map<String, Object> designSettings = (Map<String, Object>) doc.get("designSettings");
                if (designSettings.containsKey(tag) && designSettings.get(tag) != null) {
                    Map<String, Object> defaultTagSettings = (Map<String, Object>) designSettings.get(tag);
                    if (defaultTagSettings.containsKey("states") && defaultTagSettings.get("states") != null) {
                        List<Map<String, Object>> states = (List<Map<String, Object>>) defaultTagSettings.get("states");
                        for (Map<String, Object> eachState : states) {
                            if (eachState.containsKey(VALUE) && eachState.get(VALUE).toString().equalsIgnoreCase(state)) {
                                if (eachState.containsKey("suffixes")) {
                                    return (List<String>) eachState.get("suffixes");
                                } else if (eachState.containsKey("suffix")) {
                                    return Arrays.asList(eachState.get("suffix").toString());
                                }
                            }
                        }
                    }
                }
            }
        }
        return null;
    }

    public Map<String, Object> createStyle(String refNum, String tag, String displayName, Map<String, Object> data, boolean textStyle, String state, String deviceType, Map<String, Object> visibilitySettings, String theme, Map<String, Object> overlaySetiings) {
        String currentEnv = conf.getString(SRC_ENV);
        Map<String, Object> response = new HashMap<>();
        Document styleDoc = new Document(REFNUM, refNum);
        String styleId = "phw-g-" + siteUtil.normalize(displayName);
        styleDoc.append(STYLE_ID, styleId);
        List<Document> queryDocs = new ArrayList<>();
        if ("prod".equalsIgnoreCase(currentEnv) || "cmsqa1".equalsIgnoreCase(currentEnv)) {
            Document lowerEnvsiteStyleQuery = new Document(REFNUM, refNum);
            lowerEnvsiteStyleQuery.append(STYLE_ID, styleId);
            lowerEnvsiteStyleQuery.append(THEME, theme);
            List<Document> lowerEnvdocs = preProdMongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, preprodDb, lowerEnvsiteStyleQuery);
            if (!lowerEnvdocs.isEmpty()) {
                logger.error("Styles already exist with {} for {} and {} tag in lower env for {}", displayName, refNum, tag, currentEnv);
                response.put(STATUS_KEY, false);
                response.put("message", "Please try creating style with different display name");
                return response;
            }
        }
        Document siteStyleQuery = new Document(REFNUM, refNum);
        siteStyleQuery.append(STYLE_ID, styleId);
        siteStyleQuery.append(THEME, theme);
        Document globalStyleQuery = new Document(REFNUM, CANVUS_REFNUM);
        globalStyleQuery.append(STYLE_ID, styleId);
        queryDocs.add(globalStyleQuery);
        queryDocs.add(siteStyleQuery);
        List<Document> docs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, new Document("$or", queryDocs));
        if (!docs.isEmpty()) {
            logger.error("Styles already exist with {} for {} and {} tag", displayName, refNum, tag);
            response.put(STATUS_KEY, false);
            response.put("message", "Please try creating style with different display name");
            return response;
        }
        styleDoc.append(TAG, tag);
        styleDoc.append(DEVICE_TYPE, deviceType);
        styleDoc.append(DISPLAY_NAME, displayName);
        styleDoc.append("styles", data);
        if (textStyle) {
            styleDoc.append("textStyle", textStyle);
        }
        if (visibilitySettings != null && visibilitySettings.size() > 0) {
            styleDoc.append(VISIBILITY_SETTINGS, visibilitySettings);
        }
        if (overlaySetiings != null && overlaySetiings.size() > 0) {
            styleDoc.append(OVERLAY_SETTINGS, overlaySetiings);
        }
        String css = constructCss(data, styleId, tag, state, null, visibilitySettings, overlaySetiings, null, refNum, isPCMEnabledTenant(refNum, tag), theme, null, false);
        styleDoc.append("css", css);
        styleDoc.append(THEME, theme);
        mongoManager.insertDocument(styleDoc, CANVAS_SITE_CUSTOM_STYLES, db);
        if (deviceType.equalsIgnoreCase("desktop") && data.containsKey("font-size")) {
            addCalculatedValuesToDevices(refNum, theme, tag, styleId, state, MOBILE, data.get("font-size").toString(), "font-size", displayName, true, true, null, isPCMEnabledTenant(refNum, tag), false);
            addCalculatedValuesToDevices(refNum, theme, tag, styleId, state, "tab", data.get("font-size").toString(), "font-size", displayName, true, true, null, isPCMEnabledTenant(refNum, tag), false);
        }
        addIfDesktopDocIsMissing(styleDoc, isPCMEnabledTenant(refNum, tag), false);
        addCustomStylesCssToPage(refNum, theme);
        updateThemePublishStatus(refNum, theme);
        timer.schedule(new TimerTask() {

            @Override
            public void run() {
                logger.info("widget style/attribute usage data generation started at {}", new Date());
                logger.info("for refNum {}, styleId: {}, state: {}, device: {}, data: {}", refNum, styleId, state, deviceType, data);
                generateStyleAttributesMetadata(refNum, styleId, state, deviceType, data, theme);
            }
        }, 3000);
        logger.info("scheduled attribute usage data generation task for createStyle!");
        response.put(STATUS_KEY, true);
        response.put("data", styleDoc);
        return response;
    }

    public List<String> getDataPsIdsFromElement(Element element) {
        List<String> ids = new ArrayList<>();
        for (Element child : element.getElementsByAttribute(DATA_PS)) {
            ids.add(child.attr(DATA_PS));
        }
        return ids;
    }

    public void createPluginVersion(int version, String script) {
        mongoManager.findOneAndUpdate(new Document("latest", true), new Document("latest", false), "canvas_plugin_version", db);
        Document insertQuery = new Document(VERSION, version).append("script", script).append("latest", true).append("updatedDate", new Date());
        mongoManager.insertDocument(insertQuery, "canvas_plugin_version", db);
    }

    public Document getPluginVersion() {
        return mongoManager.findDocument("canvas_plugin_version", db, new Document("latest", true));
    }

    public Map<String, Boolean> getWidgetsVisibility(String refNum, String locale, String siteVariant, String pageId, String instanceId, boolean globalWidget) {
        Map<String, Boolean> response = new HashMap<>();
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        query.append(SITE_VARIANT, siteVariant);
        if (!globalWidget) {
            query.append(PAGE_ID, pageId);
        }
        query.append(INSTANCE_ID_FIELD, instanceId);
        List<Document> widgetMap = mongoManager.findAllDocuments(CANVAS_SITE_WIDGETS_VISIBILITY, db, query);
        widgetMap.forEach(widget -> {
            response.put(widget.getString(WIDGET_ID_FIELD), widget.getBoolean("enabled"));
        });
        return response;
    }

    public void updateWidgetsVisibility(String refNum, String locale, String siteVariant, String pageId, String instanceId, boolean globalWidget, Map<String, Boolean> widgetMap, JsonNode payload, Http.Request request) {
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        query.append(SITE_VARIANT, siteVariant);
        if (!globalWidget) {
            query.append(PAGE_ID, pageId);
        }
        query.append(INSTANCE_ID_FIELD, instanceId);
        mongoManager.deleteDocuments(query, CANVAS_SITE_WIDGETS_VISIBILITY, db);
        List<Document> insertDocs = new ArrayList<>();
        for (String widgetId : widgetMap.keySet()) {
            if (!widgetMap.get(widgetId)) {
                Document doc = new Document(query);
                doc.append(WIDGET_ID_FIELD, widgetId);
                doc.append("enabled", false);
                insertDocs.add(doc);
            }
        }
        if (insertDocs.size() > 0) {
            mongoManager.insertDocuments(insertDocs, CANVAS_SITE_WIDGETS_VISIBILITY, db);
        }
        if (!pageId.startsWith(BLOGARTICLE_IDENTIFIER)) {
            siteUtil.canvasApplyToLowerEnvByEndpoint(payload, "updateWidgetsVisibility", request);
        }
    }

    public long getGlobalWidgetCount(String refNum, String instanceId) {
        Document query = new Document(REFNUM, refNum).append(INSTANCE_ID_FIELD, instanceId);
        long count = mongoManager.findDocumentCount(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, query);
        return count;
    }

    public List<Document> getTagStyles(String refNum, String tag, String theme, Optional<Boolean> savedStyles, Optional<String> styleId) {
        List<String> jdPageTags = new ArrayList<>();
        Document jdPageQuery = new Document();
        Document query = new Document(REFNUM, refNum);
        query.append(TAG, tag);
        query.append(THEME, theme);
        Map<String, String> dynamicPageTagVsClasses = new HashMap<>();
        dynamicPageTagVsClasses.put("jd-page", "phw-job-description ");
        dynamicPageTagVsClasses.put("blog-page", "phw-blog-description ");
        dynamicPageTagVsClasses.put("event-page", "phw-event-description ");
        String orRegex = String.join("|", dynamicPageTagVsClasses.values());
        if (dynamicPageTagVsClasses.containsKey(tag)) {
            jdPageTags = getJdPageTags();
            query.put(STYLE_ID, DEFAULT);
        } else {
            query.put(STYLE_ID, new Document("$not", new Document("$regex", orRegex)));
        }
        if (savedStyles.isPresent()) {
            query.put("savedStyle", savedStyles.get());
        }
        if (styleId.isPresent()) {
            query.put("styleId", new Document("$ne", styleId.get()));
        }
        if (dynamicPageTagVsClasses.containsKey(tag)) {
            query.put(TAG, new Document("$in", jdPageTags));
            jdPageQuery = new Document(query);
            jdPageQuery.put(STYLE_ID, new Document("$regex", dynamicPageTagVsClasses.get(tag)).append("$options", "i"));
        } else {
            jdPageQuery = new Document(query);
        }
        List<Document> docs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, new Document("$or", Arrays.asList(query, jdPageQuery)));
        if (styleId.isPresent()) {
            query.remove("savedStyle");
            query.put("styleId", styleId.get());
            List<Document> styleIdDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, query);
            docs.addAll(styleIdDocs);
        }
        query.remove("savedStyle");
        query.put("styleId", "default");
        List<Document> defaultSiteDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, query);
        if (defaultSiteDocs != null) {
            docs.addAll(defaultSiteDocs);
        }
        query.remove(THEME);
        query.remove("styleId");
        query.append(REFNUM, CANVUS_REFNUM);
        if (dynamicPageTagVsClasses.containsKey(tag)) {
            query.put(STYLE_ID, DEFAULT);
            query.put(TAG, new Document("$in", jdPageTags));
            jdPageQuery = new Document(query);
            jdPageQuery.put(STYLE_ID, new Document("$regex", dynamicPageTagVsClasses.get(tag)).append("$options", "i"));
        } else {
            query.put(STYLE_ID, new Document("$not", new Document("$regex", orRegex)));
            jdPageQuery = new Document(query);
        }
        List<Document> defaultDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, new Document("$or", Arrays.asList(query, jdPageQuery)));
        if (defaultDocs != null) {
            defaultDocs.forEach(document -> document.remove(ID));
        }
        if (docs != null) {
            docs.forEach(document -> document.remove(ID));
            docs.addAll(defaultDocs);
            return docs;
        }
        return new ArrayList<>();
    }

    public Document getDefaultTagStyles(String refNum) {
        Document query = new Document(REFNUM, refNum);
        Document doc = mongoManager.findDocument("canvas_default_tag_styles", db, query);
        return doc;
    }

    public List<String> getJdPageTags() {
        List<String> jdPageTags = new ArrayList<>();
        Document query = new Document();
        List<String> projections = Arrays.asList("jdPageTags");
        List<Document> docs = mongoManager.findAllDocuments(CANVAS_TAG_DEFAULT_SETTINGS, db, query, projections);
        if (docs != null && !docs.isEmpty()) {
            jdPageTags = (List<String>) docs.get(0).get("jdPageTags");
        }
        return jdPageTags;
    }

    public Document getGroupingConfig() {
        return mongoManager.findDocument("canvas_style_grouping_config", db, new Document());
    }

    public void makeWidgetInstanceDirty(String refNum, String locale, String pageId, String instanceId) {
        List<String> devices = Arrays.asList(DESKTOP, MOBILE);
        for (String device : devices) {
            String pageKey = "ph:page:" + refNum + ":" + device + ":" + locale + ":" + pageId;
            String pageValue = redisManager.get(pageKey);
            if (pageValue != null) {
                Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                String pageHtml = p.getPageHtml();
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageHtml);
                Elements sectionEles = pageDoc.getElementsByTag(SECTION);
                if (sectionEles != null) {
                    for (Element sectionEle : sectionEles) {
                        if (sectionEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR) && sectionEle.hasAttr(INSTANCE_ID_FIELD) && sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                            sectionEle.attr(CANVAS_EDIT_ATTR, "true");
                        }
                    }
                }
                p.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(p).toString());
            }
        }
    }

    private String fetchWidgetViewHtmlFromPage(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device, boolean globalWidget) {
        try {
            if (globalWidget) {
                Document q = new Document(REFNUM, refNum);
                q.append(LOCALE, locale);
                q.append(PERSONA, siteVariant);
                q.append(INSTANCE_ID_FIELD, instanceId);
                q.append(DEVICE_TYPE, device);
                Document doc = mongoManager.findDocument("canvas_migrated_aurelia_globalWidgets", db, q);
                if (doc != null) {
                    String view = doc.getString("view");
                    org.jsoup.nodes.Document viewDoc = HtmlParser.parse(view);
                    Element sectionEle = viewDoc.getElementsByTag(SECTION).first();
                    if (sectionEle != null && sectionEle.hasAttr(INSTANCE_ID_FIELD) && sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                        return sectionEle.html();
                    }
                }
            } else {
                String pageKey = "ph:page:" + refNum + ":" + device + ":" + locale + ":" + pageId;
                logger.debug("Getting aurelia widget viewHtml from page {}", pageKey);
                String pageValue = redisManager.get(pageKey);
                if (pageValue != null) {
                    Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                    org.jsoup.nodes.Document pageDoc = HtmlParser.parse(p.getPageHtml());
                    Element sectionEle = pageDoc.getElementsByTag(SECTION).stream().filter(e -> e.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)).findFirst().orElse(null);
                    if (sectionEle != null && sectionEle.hasAttr(INSTANCE_ID_FIELD) && sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                        return sectionEle.html();
                    }
                }
            }
        } catch (Exception e) {
            logger.error("Exception in fetchWidgetViewHtmlFromPage : ", e);
        }
        logger.error("No element found for instance-id {}", instanceId);
        return null;
    }

    public void saveWidgetViewHtmlToPage(String refNum, String locale, String siteVariant, String pageId, String instanceId, boolean globalWidget, String widgetHtml, String mobileViewHtml) {
        try {
            List<String> channels = Arrays.asList(DESKTOP, MOBILE);
            if (globalWidget) {
                for (String device : channels) {
                    Document q = new Document(REFNUM, refNum);
                    q.append(LOCALE, locale);
                    q.append(PERSONA, siteVariant);
                    q.append(INSTANCE_ID_FIELD, instanceId);
                    q.append(DEVICE_TYPE, device);
                    String html = device.equalsIgnoreCase(DESKTOP) ? widgetHtml : mobileViewHtml;
                    if (html == null) {
                        logger.info("html is null for {}..so returning", device);
                        continue;
                    }
                    Document doc = mongoManager.findDocument("canvas_migrated_aurelia_globalWidgets", db, q);
                    if (doc != null) {
                        String view = doc.getString("view");
                        org.jsoup.nodes.Document viewDoc = HtmlParser.parse(view);
                        Element sectionEle = viewDoc.getElementsByTag(SECTION).first();
                        if (sectionEle == null || !sectionEle.hasAttr(INSTANCE_ID_FIELD) || !sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                            logger.error("No element found for instance-id {}", instanceId);
                            return;
                        }
                        sectionEle.html(html);
                        doc.put("view", viewDoc.toString());
                        doc.remove("_id");
                        mongoManager.upsert(q, new Document("$set", doc), "canvas_migrated_aurelia_globalWidgets", db);
                    }
                }
            } else {
                for (String channel : channels) {
                    String html = channel.equalsIgnoreCase(DESKTOP) ? widgetHtml : mobileViewHtml;
                    if (html == null) {
                        logger.info("html is null for {}..so returning", channel);
                        continue;
                    }
                    String pageKey = "ph:page:" + refNum + ":" + channel + ":" + locale + ":" + pageId;
                    logger.debug("Saving aurelia widget viewHtml to page {}", pageKey);
                    String pageValue = redisManager.get(pageKey);
                    if (pageValue != null) {
                        Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                        org.jsoup.nodes.Document pageDoc = HtmlParser.parse(p.getPageHtml());
                        Element sectionEle = pageDoc.getElementsByTag(SECTION).stream().filter(e -> e.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)).findFirst().orElse(null);
                        if (sectionEle == null || !sectionEle.hasAttr(INSTANCE_ID_FIELD) || !sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                            logger.error("No element found for instance-id {}", instanceId);
                            return;
                        }
                        sectionEle.html(html);
                        p.setPageHtml(pageDoc.toString());
                        redisManager.set(pageKey, Json.toJson(p).toString());
                    }
                }
            }
        } catch (Exception e) {
            logger.error("Exception in saveWidgetViewHtmlToPage : ", e);
        }
    }

    public void generateWidgetStyleMetadata(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device, Map<String, Object> data, boolean globalWidget, boolean isHF) {
        if (device == null) {
            device = DESKTOP;
        }
        Document query = getSettingsQuery(refNum, locale, siteVariant, pageId, instanceId, globalWidget, isHF);
        //delete existing metadata for widget
        mongoManager.deleteDocuments(query, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
        Set<String> styleIds = new HashSet<>();
        data.entrySet().stream().forEach(entry -> {
            if (entry.getValue() instanceof String) {
                styleIds.add((String) entry.getValue());
            } else if (entry.getValue() instanceof List) {
                List<Map<String, Object>> cards = (List<Map<String, Object>>) entry.getValue();
                for (Map<String, Object> card : cards) {
                    card.entrySet().stream().forEach(tagStyle -> {
                        if (tagStyle.getValue() instanceof String) {
                            styleIds.add((String) tagStyle.getValue());
                        } else {
                            Map<String, Object> objData = (Map<String, Object>) tagStyle.getValue();
                            styleIds.add((String) objData.get(STYLE_ID));
                        }
                    });
                }
            } else {
                Map<String, Object> objData = (Map<String, Object>) entry.getValue();
                styleIds.add((String) objData.get(STYLE_ID));
            }
        });
        List<Document> metadataDocs = new ArrayList<>();
        for (String styleId : styleIds) {
            Document metadataDoc = new Document(REFNUM, refNum);
            metadataDoc.put(LOCALE, locale);
            metadataDoc.put(SITE_VARIANT, siteVariant);
            metadataDoc.put(DATA.DEVICE, device);
            metadataDoc.put(PAGE_ID, pageId);
            metadataDoc.put(INSTANCE_ID_FIELD, instanceId);
            metadataDoc.put(STYLE_ID, styleId);
            if (globalWidget || isHF) {
                metadataDoc.remove(PAGE_ID);
                metadataDoc.append(GLOBAL_WIDGET, true);
            }
            metadataDocs.add(metadataDoc);
        }
        mongoManager.insertDocuments(metadataDocs, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
    }

    public boolean checKIfAgWidget(String refNum, Element sectionEle) {
        try {
            TenantDetails td = siteUtil.getTenantDetails(refNum);
            if (td.isMigratedSite()) {
                return sectionEle.hasAttr("aurelia-widget");
            }
        } catch (Exception e) {
            logger.error("Exception in checKIfAgWidget refNum {} slectionEle{}", refNum, sectionEle);
        }
        return false;
    }

    public Map<String, Object> deleteAureliaWidget(CanvasDeleteWidgetRequest canvasDeleteRequest, Element sectionEle, Element mobileSectionEle) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS, true);
        try {
            String refNum = canvasDeleteRequest.getRefNum();
            String locale = canvasDeleteRequest.getLocale();
            String siteVariant = canvasDeleteRequest.getSiteVariant();
            String pageId = canvasDeleteRequest.getPageId();
            String instanceId = canvasDeleteRequest.getInstanceId();
            String deviceMode = canvasDeleteRequest.getDeviceMode();
            String widgetId = canvasDeleteRequest.getWidgetId();
            if (!canvasDeleteRequest.isGlobalWidget()) {
                org.bson.Document deleteWidgetDoc = new Document();
                String widgetName = "";
                if (sectionEle != null) {
                    deleteWidgetDoc.append(SECTION_HTML_TEXT, sectionEle.toString());
                } else {
                    deleteWidgetDoc.append(SECTION_HTML_TEXT, null);
                }
                if (mobileSectionEle != null) {
                    deleteWidgetDoc.append(SECTION_HTML_MOBILE_TEXT, mobileSectionEle.toString());
                } else {
                    deleteWidgetDoc.append(SECTION_HTML_MOBILE_TEXT, null);
                }
                if (deviceMode.equalsIgnoreCase(MOBILE) && mobileSectionEle != null) {
                    if (mobileSectionEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
                        widgetName = mobileSectionEle.attr(CANVAS_STATIC_WIDGET_ATTR);
                        deleteWidgetDoc.append(TYPE, STATIC);
                    } else if (mobileSectionEle.hasAttr(CANVAS_FUNC_WIDGET_ATTR)) {
                        widgetName = mobileSectionEle.attr(CANVAS_FUNC_WIDGET_ATTR);
                        deleteWidgetDoc.append(TYPE, FUNCTIONAL);
                    }
                } else if (deviceMode.equalsIgnoreCase(DESKTOP) && sectionEle != null) {
                    if (sectionEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
                        widgetName = sectionEle.attr(CANVAS_STATIC_WIDGET_ATTR);
                        deleteWidgetDoc.append(TYPE, STATIC);
                    } else if (sectionEle.hasAttr(CANVAS_FUNC_WIDGET_ATTR)) {
                        widgetName = sectionEle.attr(CANVAS_FUNC_WIDGET_ATTR);
                        deleteWidgetDoc.append(TYPE, FUNCTIONAL);
                    }
                }
                deleteWidgetDoc.put(PANEL_DISPLAYNAME, widgetName);
                deleteWidgetDoc.put("name", widgetName);
                deleteWidgetDoc.put(WIDGET_TAG, widgetName);
                org.bson.Document siteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId);
                Document settingsDoc = mongoManager.findDocument(CANVAS_SITE_INSTANCE_SETTINGS, db, siteQuery);
                if (settingsDoc != null) {
                    deleteWidgetDoc.put("settings", settingsDoc.get("settings"));
                    Set<String> styleIdsSet = new HashSet<>();
                    widgetUtil.extractStyleIds((Map<String, Object>) settingsDoc.get("settings"), styleIdsSet);
                    deleteWidgetDoc.append(STYLE_IDS, styleIdsSet);
                }
                String oldGroup = "other";
                Document oldGroupDoc = mongoManager.findDocument(CANVAS_WIDGET_PANEL, db, new Document(REFNUM, refNum).append(WIDGET_ID_FIELD, widgetId));
                if (oldGroupDoc != null && oldGroupDoc.containsKey(GROUP)) {
                    oldGroup = oldGroupDoc.getString(GROUP);
                }
                deleteWidgetDoc.append(AURELIA_MIGRATED_WIDGET_TEXT, true);
                deleteWidgetDoc.append(WIDGET_ID_FIELD, widgetId);
                deleteWidgetDoc.append("deletedDate", new Date());
                deleteWidgetDoc.append(REFNUM, refNum);
                deleteWidgetDoc.append(LOCALE, locale);
                deleteWidgetDoc.append(SITE_VARIANT, siteVariant);
                deleteWidgetDoc.append(PAGE_ID, pageId);
                deleteWidgetDoc.append(INSTANCE_ID_FIELD, instanceId);
                deleteWidgetDoc.append(PAGENAME, canvasDeleteRequest.getPageName());
                deleteWidgetDoc.append("nextSiblingId", canvasDeleteRequest.getNextSiblingId());
                deleteWidgetDoc.append("previousSiblingId", canvasDeleteRequest.getPreviousSiblingId());
                deleteWidgetDoc.append("parentElementId", canvasDeleteRequest.getParentId());
                deleteWidgetDoc.append(SAVED_VIEW, true);
                deleteWidgetDoc.append("activityId", RandomStringUtils.randomAlphanumeric(8));
                deleteWidgetDoc.append("targetDevice", deviceMode);
                deleteWidgetDoc.append("originalGroup", oldGroup);
                mongoManager.insertDocument(deleteWidgetDoc, CANVAS_DELETED_WIDGETS, db);
                deleteWidgetMetadata(canvasDeleteRequest);
            } else {
                deleteGlobalWidgetMetadata(canvasDeleteRequest);
            }
        } catch (Exception e) {
            logger.error("Exception while deleting aurelia widget {}", canvasDeleteRequest);
        }
        return response;
    }

    public Map<String, Object> deleteWidget(CanvasDeleteWidgetRequest canvasDeleteRequest) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        try {
            String refNum = canvasDeleteRequest.getRefNum();
            String locale = canvasDeleteRequest.getLocale();
            String siteVariant = canvasDeleteRequest.getSiteVariant();
            String pageId = canvasDeleteRequest.getPageId();
            String instanceId = canvasDeleteRequest.getInstanceId();
            String deviceMode = canvasDeleteRequest.getDeviceMode();
            String widgetId = canvasDeleteRequest.getWidgetId();
            boolean agWidget = false;
            String oldGroup = "";
            Document oldGroupDoc = mongoManager.findDocument(CANVAS_WIDGET_PANEL, db, new Document(REFNUM, refNum).append(WIDGET_ID_FIELD, widgetId));
            if (oldGroupDoc != null && oldGroupDoc.containsKey(GROUP)) {
                oldGroup = oldGroupDoc.getString(GROUP);
            }
            Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
            if (widgetDoc == null) {
                widgetDoc = mongoManager.findDocument(CANVAS_SITEWIDGETS, db, new Document(WIDGET_ID_FIELD, widgetId).append(REFNUM, refNum));
            }
            List<String> devices;
            if (DESKTOP.equalsIgnoreCase(deviceMode)) {
                devices = Arrays.asList(DESKTOP, MOBILE);
            } else {
                devices = Arrays.asList(deviceMode);
            }
            Element desktopSectionElem = null;
            Element mobileSectionElem = null;
            for (String device : devices) {
                String pageKey = SiteUtil.constructPhPageKey(refNum, device, locale, pageId);
                String pageContent = redisManager.get(pageKey);
                Page page = Json.fromJson(Json.parse(pageContent), Page.class);
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
                Element phPageEle = pageDoc.getElementsByClass("ph-page").first();
                Element sectionEle = phPageEle.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId).stream().filter(el -> el.tagName().equals(SECTION)).findFirst().orElse(null);
                //for migrated widgets in migrated site
                if (sectionEle != null && checKIfAgWidget(refNum, sectionEle)) {
                    agWidget = true;
                    if (deviceMode.equals(MOBILE)) {
                        addHiddenClassInMobilePageDuringDelete(refNum, locale, pageId, instanceId, sectionEle);
                    } else {
                        sectionEle.remove();
                    }
                    if (device.equals(DESKTOP)) {
                        desktopSectionElem = sectionEle;
                    } else {
                        mobileSectionElem = sectionEle;
                    }
                    page.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(page).toString());
                } else if (sectionEle != null) {
                    widgetDoc.append("sectionHtml", sectionEle.toString());
                    if (deviceMode.equals(MOBILE)) {
                        addHiddenClassInMobilePageDuringDelete(refNum, locale, pageId, instanceId, sectionEle);
                    } else {
                        sectionEle.remove();
                    }
                    page.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(page).toString());
                }
            }
            if (agWidget && widgetDoc == null) {
                deleteAureliaWidget(canvasDeleteRequest, desktopSectionElem, mobileSectionElem);
            }
            if (!canvasDeleteRequest.isGlobalWidget()) {
                if (agWidget) {
                    logger.debug("Aurelia widget deleted successfully");
                    // aurelia widgets not adding to the deleted widgets collection
                    return response;
                }
                String displayName = widgetDoc.getString("panelDisplayName") != null ? widgetDoc.getString("panelDisplayName") : widgetDoc.getString("displayName");
                widgetDoc.put(PANEL_DISPLAYNAME, displayName);
                widgetDoc.put("name", displayName);
                //                if (widgetDoc.get("type").equals("Static")) {
                //
                //                    Document viewDoc =
                //                            mongoManager.findDocument(CANVAS_GLOBALWIDGETVIEWS, db,
                //                                    new Document(VIEW_ID, viewId).append(LATEST, true));
                //                    if (viewDoc == null) {
                //                        viewDoc = mongoManager.findDocument(CANVAS_SITEWIDGETVIEWS, db,
                //                                new Document(REFNUM, refNum).append(VIEW_ID, viewId).append(LATEST, true));
                //                    }
                //
                //                    String renderedHtml =
                //                            getCanvasRenderedHtml(refNum, locale, siteVariant, canvasDeleteRequest.getDeviceMode(),
                //                                    pageId, instanceId, viewDoc, false);
                //                    widgetDoc.put("viewHtml", renderedHtml);
                //                }
                org.bson.Document siteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId);
                Document settingsDoc = mongoManager.findDocument(CANVAS_SITE_INSTANCE_SETTINGS, db, siteQuery);
                if (settingsDoc != null) {
                    widgetDoc.put("settings", settingsDoc.get("settings"));
                    Set<String> styleIdsSet = new HashSet<>();
                    widgetUtil.extractStyleIds((Map<String, Object>) settingsDoc.get("settings"), styleIdsSet);
                    widgetDoc.append(STYLE_IDS, styleIdsSet);
                }
                widgetDoc.remove(ID);
                widgetDoc.append("deletedDate", new Date());
                widgetDoc.append(REFNUM, refNum);
                widgetDoc.append(LOCALE, locale);
                widgetDoc.append(SITE_VARIANT, siteVariant);
                widgetDoc.append(PAGE_ID, pageId);
                widgetDoc.append(INSTANCE_ID_FIELD, instanceId);
                widgetDoc.append(PAGENAME, canvasDeleteRequest.getPageName());
                widgetDoc.append("nextSiblingId", canvasDeleteRequest.getNextSiblingId());
                widgetDoc.append("previousSiblingId", canvasDeleteRequest.getPreviousSiblingId());
                widgetDoc.append("parentElementId", canvasDeleteRequest.getParentId());
                widgetDoc.append(SAVED_VIEW, true);
                widgetDoc.append("activityId", RandomStringUtils.randomAlphanumeric(8));
                widgetDoc.append("targetDevice", deviceMode);
                widgetDoc.append("originalGroup", oldGroup);
                mongoManager.insertDocument(widgetDoc, CANVAS_DELETED_WIDGETS, db);
                deleteWidgetMetadata(canvasDeleteRequest);
            } else {
                deleteGlobalWidgetMetadata(canvasDeleteRequest);
            }
        } catch (Exception ex) {
            response.put(STATUS_KEY, false);
            logger.error("Exception in  deleteWidget {}", ex);
        }
        return response;
    }

    public Map<String, Object> deleteFromPanel(String refNum, String locale, String siteVariant, String pageId, String instanceId, String widgetId) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        try {
            if (mongoManager.checkIfDocumentExists(CANVAS_SAVED_WIDGETS, db, new Document(REFNUM, refNum).append(WIDGET_ID_FIELD, widgetId))) {
                logger.info("removing saved widget from panel {}", instanceId);
                mongoManager.deleteDocuments(new Document(REFNUM, refNum).append(WIDGET_ID_FIELD, widgetId), CANVAS_SAVED_WIDGETS, db);
                deleteSavedWidgetInCaasDB(refNum, widgetId);
            } else {
                if (pageId == null) {
                    throw new PhenomException("PageId not provided in deleteFromPanel method");
                }
                logger.info("removing deleted widget from panel {}", instanceId);
                org.bson.Document deleteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId);
                mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_INSTANCE_SETTINGS, db);
                mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
                mongoManager.deleteDocuments(deleteQuery, CANVAS_CAAS_SITE_CONTENT, db);
                mongoManager.deleteDocuments(deleteQuery, CANVAS_DELETED_WIDGETS, db);
                deleteQuery.remove(SITE_VARIANT);
                deleteQuery.append(PERSONA, siteVariant);
                mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_CONTENT, db);
            }
        } catch (Exception ex) {
            response.put(STATUS_KEY, false);
            logger.error("Exception in  deleteFromPanel {}", ex);
        }
        return response;
    }

    private void deleteSavedWidgetInCaasDB(String refNum, String widgetId) {
        try {
            Map<String, Object> payload = new HashMap<>();
            payload.put(REFNUM, refNum);
            payload.put("widgetId", widgetId);
            String caasDeleteWidgetUrl = conf.getConfig("template.api").getString("base");
            caasDeleteWidgetUrl = caasDeleteWidgetUrl + "canvas/deleteSavedWidget";
            siteUtil.sendPostAsync(caasDeleteWidgetUrl, payload);
        } catch (Exception ex) {
            logger.error("Exception in deleteSavedWidgetInCaasDB {}", ex);
        }
    }

    public void deleteGlobalWidgetMetadata(CanvasDeleteWidgetRequest canvasDeleteRequest) {
        Document deleteQuery = new Document(REFNUM, canvasDeleteRequest.getRefNum()).append(LOCALE, canvasDeleteRequest.getLocale());
        deleteQuery.append(SITE_VARIANT, canvasDeleteRequest.getSiteVariant());
        deleteQuery.append(INSTANCE_ID_FIELD, canvasDeleteRequest.getInstanceId());
        deleteQuery.append(PAGE_ID, canvasDeleteRequest.getPageId());
        //        deleteQuery.append("targetId", canvasDeleteRequest.getTargetId());
        mongoManager.deleteDocument(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, deleteQuery);
    }

    public Element getSecElemFromPage(String pageKey, String instanceId) {
        Element sectionEle = null;
        try {
            String pageContent = redisManager.get(pageKey);
            if (pageContent != null) {
                Page page = Json.fromJson(Json.parse(pageContent), Page.class);
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
                Element phPageEle = pageDoc.getElementsByClass("ph-page").first();
                sectionEle = phPageEle.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId).first();
            } else {
                logger.debug("pageContent empty for this pageKey {}", pageKey);
            }
        } catch (Exception e) {
            logger.error("Exception in getSectionElementFromPage {} ", e, pageKey);
        }
        return sectionEle;
    }

    private Document generateWidgetDocForAgWgt(String instanceId, String pageKey) {
        org.bson.Document widgetDoc = new Document();
        widgetDoc.put("isAureliaMigrated", true);
        try {
            Element sectionEle = getSecElemFromPage(pageKey, instanceId);
            Element mobileEle = getSecElemFromPage(pageKey.replace(DESKTOP, MOBILE), instanceId);
            if (sectionEle != null) {
                if (sectionEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
                    widgetDoc.append(TYPE, "Static");
                    widgetDoc.append(VIEW_ID, sectionEle.attr(CANVAS_STATIC_WIDGET_ATTR));
                    widgetDoc.append("desktop_view", sectionEle.toString());
                    if (mobileEle != null)
                        widgetDoc.append("mobile_view", mobileEle.toString());
                } else if (sectionEle.hasAttr(CANVAS_FUNC_WIDGET_ATTR)) {
                    widgetDoc.append(TYPE, "Functional");
                    widgetDoc.append(VIEW_ID, sectionEle.attr(CANVAS_FUNC_WIDGET_ATTR));
                    widgetDoc.append("desktop_view", sectionEle.toString());
                    if (mobileEle != null)
                        widgetDoc.append("mobile_view", mobileEle.toString());
                } else {
                    logger.debug("Widget identifier not found pagekey {} and instanceid {}", pageKey, instanceId);
                }
                widgetDoc.put("_id", "");
                widgetDoc.put("updatedBy", "");
            } else {
                logger.debug("Section element not found instance and pageKey {} {} ", instanceId, pageKey);
            }
        } catch (Exception e) {
            logger.error("Exception while generating widgetDoc {}", pageKey, e);
        }
        return widgetDoc;
    }

    public Map<String, Object> saveWidget(String refNum, String locale, String siteVariant, String device, String pageId, String instanceId, String widgetId, String name, String uniqueId, boolean isMigratedWidget) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        try {
            Document checkQuery = new Document(REFNUM, refNum);
            if (isDisplayNamePresent(checkQuery, CANVAS_SAVED_WIDGETS, "name", name)) {
                response.put(STATUS_KEY, false);
                response.put("message", "Saved Widget already exists with the given name");
                return response;
            }
            Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
            if (isMigratedWidget && !pageId.equalsIgnoreCase("copyId")) {
                widgetDoc = new Document(generateWidgetDocForAgWgt(instanceId, SiteUtil.constructPhPageKey(refNum, device, locale, pageId)));
            }
            if (widgetDoc == null) {
                widgetDoc = mongoManager.findDocument(CANVAS_SITEWIDGETS, db, new Document(WIDGET_ID_FIELD, widgetId).append(REFNUM, refNum));
            }
            widgetDoc.append(REFNUM, refNum);
            widgetDoc.append(LOCALE, locale);
            widgetDoc.append(SITE_VARIANT, siteVariant);
            widgetDoc.append(PAGE_ID, pageId);
            widgetDoc.append(INSTANCE_ID_FIELD, instanceId);
            widgetDoc.remove(ID);
            if (uniqueId == null) {
                uniqueId = SiteUtil.generateUniqueId(8);
                response.put("uniqueId", uniqueId);
            }
            String viewId = widgetDoc.getString(VIEW_ID);
            widgetDoc.put(WIDGET_ID_FIELD, uniqueId);
            widgetDoc.put("parent_widget_id", widgetId);
            widgetDoc.put(VIEW_ID, viewId + "-" + uniqueId);
            widgetDoc.put(PANEL_DISPLAYNAME, name);
            widgetDoc.put("name", name);
            widgetDoc.append("createdDate", new Date());
            widgetDoc.append(SAVED_VIEW, true);
            widgetDoc.remove("updatedBy");
            if (widgetDoc.get("type").equals("Static")) {
                org.bson.Document siteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId);
                Document settingsDoc = mongoManager.findDocument(CANVAS_SITE_INSTANCE_SETTINGS, db, siteQuery);
                if (settingsDoc != null) {
                    widgetDoc.put("settings", settingsDoc.get("settings"));
                    Set<String> styleIdsSet = new HashSet<>();
                    widgetUtil.extractStyleIds((Map<String, Object>) settingsDoc.get("settings"), styleIdsSet);
                    widgetDoc.append(STYLE_IDS, styleIdsSet);
                }
                org.bson.Document contentQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(PERSONA, siteVariant).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId);
                List<Document> contentDocs = mongoManager.findAllDocuments(CANVAS_SITE_CONTENT, db, contentQuery);
                if (contentDocs != null && contentDocs.size() > 0) {
                    contentDocs.forEach(contentDoc -> {
                        contentDoc.remove(ID);
                    });
                    widgetDoc.put(TAG_CONTENT, contentDocs);
                }
                siteQuery.append("enabled", true);
                List<Document> caasContents = mongoManager.findAllDocuments(CANVAS_CAAS_SITE_CONTENT, db, siteQuery);
                String defaultLocale = siteUtil.getTenantDefaultLocale(refNum);
                for (Document caasContent : caasContents) {
                    caasContent.remove("_id");
                    Map<String, Object> query = caasContent.get("query", Map.class);
                    if (caasContent.getString("filterType").equals("static")) {
                        if (query.containsKey("selectedItems")) {
                            List<String> selectedItems = (List<String>) query.get("selectedItems");
                            List<String> newSelectedItems = new ArrayList<>();
                            selectedItems.forEach(contentId -> {
                                String cloneContentId = cloneCaasContentId(refNum, locale, defaultLocale, contentId, true);
                                if (cloneContentId != null) {
                                    newSelectedItems.add(cloneContentId);
                                }
                            });
                            query.put("selectedItems", newSelectedItems);
                        }
                    }
                    caasContent.put("query", query);
                }
                if (caasContents.size() > 0) {
                    widgetDoc.put("caasContent", caasContents);
                }
                String renderedHtml = getCanvasRenderedHtml(refNum, locale, siteVariant, device, pageId, instanceId, isMigratedWidget, null);
                if (renderedHtml != null) {
                    Elements innerWidgets = Jsoup.parse(renderedHtml).getElementsByAttribute(CANVAS_FUNC_WIDGET_ATTR);
                    if (innerWidgets != null && innerWidgets.size() > 0) {
                        widgetDoc.put("innerWidgets", true);
                        for (Element innerWidget : innerWidgets) {
                            String innerWidgetId = innerWidget.attr(CANVAS_FUNC_WIDGET_ATTR);
                            String innerInstanceId = innerWidget.attr(INSTANCE_ID_FIELD);
                            saveFunctionalWidgetSnapshot(refNum, locale, siteVariant, device, pageId, innerWidgetId, innerInstanceId, uniqueId);
                        }
                    }
                }
                widgetDoc.put("viewHtml", renderedHtml);
            } else if (widgetDoc.get("type").equals("Functional")) {
                saveFunctionalWidgetSnapshot(refNum, locale, siteVariant, device, pageId, widgetId, instanceId, uniqueId);
            }
            mongoManager.insertDocument(widgetDoc, CANVAS_SAVED_WIDGETS, db);
            refreshWidgetScreenshot(refNum, locale, device, uniqueId);
        } catch (Exception ex) {
            response.put(STATUS_KEY, false);
            logger.error("Exception in  saveWidget {}", ex);
        }
        return response;
    }

    public void saveFunctionalWidgetSnapshot(String refNum, String locale, String siteVariant, String device, String pageId, String widgetId, String instanceId, String uniqueId) {
        try {
            logger.info("saveFunctionalWidgetSnapshot for {} {} {} {} {} {}", refNum, locale, pageId, widgetId, instanceId, uniqueId);
            String saveFuncWidgetUrl = conf.getConfig("template.api").getString("base");
            if (saveFuncWidgetUrl == null) {
                throw new PhenomException("template.api is not configured in conf file..");
            }
            saveFuncWidgetUrl = saveFuncWidgetUrl + "canvas/saveFunctionalWidget";
            Map<String, Object> params = new HashMap<>();
            params.put(REFNUM, refNum);
            params.put(LOCALE, locale);
            params.put(SITE_VARIANT, siteVariant);
            params.put(DEVICE_TYPE, device);
            params.put(PAGE_ID, pageId);
            params.put("uniqueId", uniqueId);
            params.put("instanceId", instanceId);
            params.put("parentWidgetId", widgetId);
            JsonNode response = remoteServiceCaller.sendPostSync(saveFuncWidgetUrl, params);
            logger.debug("response from saveFunctionalWidget for {} is {}", params, response);
        } catch (Exception ex) {
            logger.error("Exception in saveFunctionalWidget {}", ex);
        }
    }

    private void addSavedWidgetFunctional(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device, String uniqueId) {
        try {
            logger.info("addSavedWidgetFunctional for {} {} {} {}", refNum, locale, instanceId, uniqueId);
            String saveFuncWidgetUrl = conf.getConfig("template.api").getString("base");
            if (saveFuncWidgetUrl == null) {
                throw new PhenomException("template.api is not configured in conf file..");
            }
            saveFuncWidgetUrl = saveFuncWidgetUrl + "canvas/addSavedWidgetFunctional";
            Map<String, Object> params = new HashMap<>();
            params.put(REFNUM, refNum);
            params.put(LOCALE, locale);
            params.put(SITE_VARIANT, siteVariant);
            params.put(DEVICE_TYPE, device);
            params.put(PAGE_ID, pageId);
            params.put("uniqueId", uniqueId);
            params.put("instanceId", instanceId);
            JsonNode response = remoteServiceCaller.sendPostSync(saveFuncWidgetUrl, params);
            logger.debug("response from addSavedWidgetFunctional for {} is {}", params, response);
        } catch (Exception ex) {
            logger.error("Exception in addSavedWidgetFunctional {}", ex);
        }
    }

    private void refreshWidgetScreenshot(String refNum, String locale, String device, String uniqueId) throws PhenomException {
        String refreshScreenshotUrl = conf.getString(CONFIG.REFRESH_SCREENSHORT_URL);
        if (refreshScreenshotUrl == null) {
            throw new PhenomException("refreshScreenshot.url is not configured in conf file..");
        }
        Map<String, Object> payLoad = new HashMap<>();
        payLoad.put("widgetId", uniqueId);
        payLoad.put("channel", device);
        payLoad.put(REFNUM, refNum);
        payLoad.put(LOCALE, locale);
        remoteServiceCaller.sendPostASync(refreshScreenshotUrl, payLoad);
    }

    private String cloneCaasContentId(String refNum, String locale, String targetLocale, String contentId, boolean defaultContent) {
        try {
            String contentIdUrl = conf.getString(CONFIG.CLONECONTENTID_URL);
            if (contentIdUrl == null) {
                throw new PhenomException("clonecontentid.url is not configured in conf file..");
            }
            Map<String, Object> payLoad = new HashMap<>();
            payLoad.put(DATA.CONTENT_ID, contentId);
            payLoad.put(SRC_REFNUM, refNum);
            payLoad.put(REFNUM, refNum);
            payLoad.put(SRC_LOCALE, locale);
            payLoad.put(LOCALE, targetLocale);
            payLoad.put("defaultContent", defaultContent);
            payLoad.put("cloneNonAssetReferences", true);
            JsonNode response = remoteServiceCaller.sendPostSync(contentIdUrl, payLoad);
            logger.info(" Response from Content service is : {}", response);
            if (response != null && response.has("status") && (response.get("status").asText()).equalsIgnoreCase(SUCCESS)) {
                return response.get("data").get(CONTENT_ID).asText();
            } else {
                logger.error(" Error from Content service : {}", response);
                return null;
            }
        } catch (Exception ex) {
            return null;
        }
    }

    String updateInstanceInView(String newInstanceId, String newGlbWgtId, String view) {
        org.jsoup.nodes.Document viewDoc = HtmlParser.parse(view);
        viewDoc.attr(INSTANCE_ID_FIELD, newInstanceId);
        viewDoc.attr("global-widget-id", newGlbWgtId);
        for (Element child : viewDoc.children()) {
            if (child.hasAttr(INSTANCE_ID_FIELD))
                child.attr(INSTANCE_ID_FIELD, newInstanceId);
            if (child.hasAttr("global-widget-id"))
                ;
            child.attr("global-widget-id", newGlbWgtId);
        }
        return viewDoc.toString();
    }

    private void createGlobalWidgetDocForAgWgt(Document widgetDoc) {
        try {
            if (widgetDoc.getString(PAGE_ID).equalsIgnoreCase("copyId")) {
                String newInstanceId = widgetDoc.getString("newInstanceId");
                String oldGlbWgtId = widgetDoc.getString("oldGlbWgtId");
                String newGlbWgtId = widgetDoc.getString("globalWidgetId");
                List<Document> existingDocs = mongoManager.findAllDocuments(Constants.CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db, new Document(REFNUM, widgetDoc.getString(REFNUM)).append("globalWidgetId", oldGlbWgtId));
                for (Document eachDoc : existingDocs) {
                    eachDoc.remove("_id");
                    eachDoc.put("globalWidgetId", newGlbWgtId);
                    eachDoc.put(INSTANCE_ID_FIELD, newInstanceId);
                    eachDoc.put("name", widgetDoc.getString("name"));
                    eachDoc.put(VIEW, updateInstanceInView(newInstanceId, newGlbWgtId, eachDoc.getString(VIEW)));
                    mongoManager.insertDocument(eachDoc, Constants.CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db);
                }
            } else {
                Document insertDoc = new Document(REFNUM, widgetDoc.getString(REFNUM));
                insertDoc.put(LOCALE, widgetDoc.getString(LOCALE));
                insertDoc.put(PERSONA, widgetDoc.getString(PERSONA));
                insertDoc.put(INSTANCE_ID_FIELD, widgetDoc.getString("newInstanceId"));
                insertDoc.put("globalWidgetId", widgetDoc.getString("globalWidgetId"));
                insertDoc.put(NAME, widgetDoc.getString(NAME));
                insertDoc.put("isAureliaMigrated", true);
                insertDoc.put("globalWidget", true);
                insertDoc.put("type", widgetDoc.getString("type"));
                insertDoc.put(VIEW, updateInstanceInView(widgetDoc.getString("newInstanceId"), widgetDoc.getString("globalWidgetId"), widgetDoc.getString("desktop_view")));
                insertDoc.put(DEVICE_TYPE, DESKTOP);
                mongoManager.insertDocument(insertDoc, Constants.CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db);
                insertDoc.remove(ID);
                insertDoc.put(VIEW, updateInstanceInView(widgetDoc.getString("newInstanceId"), widgetDoc.getString("globalWidgetId"), widgetDoc.getString("mobile_view")));
                insertDoc.put(DEVICE_TYPE, MOBILE);
                mongoManager.insertDocument(insertDoc, Constants.CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db);
            }
        } catch (Exception e) {
            logger.error("Exception in createGlobalWidgetDocForAgWgt widgetDoc {} : ", widgetDoc, e);
        }
    }

    public Map<String, Object> convertToGlobalWidget(String refNum, String locale, String siteVariant, String pageId, String instanceId, String widgetId, String targetId, String name, String globalWidgetId, String newInstanceId, String oldGlobalWidgetId) {
        Map<String, Object> response = new HashMap<>();
        boolean isMigratedWgt = false;
        boolean isMigratedSite = isCanvasMigratedSite(refNum, Optional.of(siteVariant));
        response.put(STATUS_KEY, true);
        try {
            Document checkQuery = new Document(REFNUM, refNum);
            if (isDisplayNamePresent(checkQuery, CANVAS_SITE_GLOBAL_WIDGET_PANEL, "name", name) || (isMigratedSite && isDisplayNamePresent(checkQuery, CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, "name", name))) {
                response.put(STATUS_KEY, false);
                response.put("message", "Global Widget already exists with the given name");
                return response;
            }
            String pageKey = SiteUtil.constructPhPageKey(refNum, "desktop", locale, pageId);
            Document widgetDoc = new Document();
            if (isMigratedSite && pageId.equalsIgnoreCase("copyId")) {
                logger.info("duplicate global widget ");
                Document query = new Document(REFNUM, refNum).append("globalWidgetId", oldGlobalWidgetId);
                Document doc = mongoManager.findDocument(Constants.CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db, query);
                if (doc != null) {
                    isMigratedWgt = true;
                    org.jsoup.nodes.Document view = HtmlParser.parse(doc.getString("view"));
                    if (view.hasAttr(CANVAS_STATIC_WIDGET_ATTR)) {
                        widgetDoc.put(TYPE, "static");
                        widgetDoc.put(VIEW_ID, view.attr(CANVAS_STATIC_WIDGET_ATTR));
                    } else {
                        widgetDoc.put(TYPE, "functional");
                        widgetDoc.put(VIEW_ID, view.attr(CANVAS_FUNC_WIDGET_ATTR));
                    }
                    widgetDoc.put("_id", "");
                    widgetDoc.put("updatedBy", "");
                }
            } else {
                isMigratedWgt = checKIfAgWidget(refNum, getSecElemFromPage(pageKey, instanceId));
                if (isMigratedWgt && !pageId.equalsIgnoreCase("copyId"))
                    widgetDoc = new Document(generateWidgetDocForAgWgt(instanceId, pageKey));
            }
            if (!isMigratedWgt) {
                widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
                if (widgetDoc == null) {
                    widgetDoc = mongoManager.findDocument(CANVAS_SITEWIDGETS, db, new Document(WIDGET_ID_FIELD, widgetId).append(REFNUM, refNum));
                }
            }
            if (widgetDoc == null) {
                response.put(STATUS_KEY, false);
                response.put("message", "Widget not found!");
                return response;
            }
            if (globalWidgetId == null) {
                globalWidgetId = SiteUtil.generateUniqueId();
                response.put("uniqueId", globalWidgetId);
            }
            if (newInstanceId == null) {
                newInstanceId = SiteUtil.generateUniqueId();
                response.put("newInstanceId", newInstanceId);
            }
            if (isMigratedSite && isMigratedWgt) {
                widgetDoc.put("isAureliaMigrated", true);
                widgetDoc.put(REFNUM, refNum);
                widgetDoc.put(LOCALE, locale);
                widgetDoc.put(PERSONA, siteVariant);
                widgetDoc.put(PAGE_ID, pageId);
                widgetDoc.put("oldGlbWgtId", oldGlobalWidgetId);
                widgetDoc.put("globalWidgetId", globalWidgetId);
                widgetDoc.put("newInstanceId", newInstanceId);
                widgetDoc.put("name", name);
                createGlobalWidgetDocForAgWgt(widgetDoc);
            }
            String type = widgetDoc.getString("type");
            if (type.equalsIgnoreCase("static")) {
                String renderedHtml = getCanvasRenderedHtml(refNum, locale, siteVariant, DESKTOP, pageId, instanceId, isMigratedWgt, null);
                if (renderedHtml != null) {
                    Elements innerWidgets = Jsoup.parse(renderedHtml).getElementsByAttribute(CANVAS_FUNC_WIDGET_ATTR);
                    if (innerWidgets != null && !innerWidgets.isEmpty()) {
                        for (Element innerWidget : innerWidgets) {
                            String innerInstanceId = innerWidget.attr(INSTANCE_ID_FIELD);
                            String funcNewInstanceId = innerInstanceId.replace(innerInstanceId.split("\\$\\$")[0], newInstanceId);
                            convertFuncWidgetToGlobal(refNum, locale, siteVariant, pageId, innerInstanceId, funcNewInstanceId);
                        }
                    }
                }
            } else {
                convertFuncWidgetToGlobal(refNum, locale, siteVariant, pageId, instanceId, newInstanceId);
            }
            removePageIdForGlobalWidgetAndCopy(refNum, locale, siteVariant, pageId, instanceId, newInstanceId);
            Document insertQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant);
            insertQuery.append(WIDGET_ID_FIELD, widgetId);
            insertQuery.append(INSTANCE_ID_FIELD, newInstanceId);
            insertQuery.append("name", name);
            insertQuery.append("actualPageId", pageId);
            insertQuery.append("type", type);
            insertQuery.append("globalWidgetId", globalWidgetId);
            if (isMigratedWgt) {
                insertQuery.append("isAureliaMigrated", isMigratedWgt);
            }
            if (!isMigratedWgt)
                mongoManager.insertDocument(insertQuery, CANVAS_SITE_GLOBAL_WIDGET_PANEL, db);
            if (targetId != null) {
                insertQuery.remove("name");
                insertQuery.remove("type");
                insertQuery.remove("actualPageId");
                insertQuery.append(PAGE_ID, pageId);
                insertQuery.append("targetId", targetId);
                if (isMigratedWgt) {
                    insertQuery.append("isAureliaMigrated", isMigratedWgt);
                }
                mongoManager.insertDocument(insertQuery, CANVAS_SITE_GLOBAL_WIDGET_METADATA, db);
                makeWidgetGlobalTrue(refNum, locale, siteVariant, pageId, globalWidgetId, targetId, newInstanceId);
            }
        } catch (Exception ex) {
            response.put(STATUS_KEY, false);
            logger.error("Exception in  convertToGlobalWidget {}", ex);
        }
        return response;
    }

    public void convertFuncWidgetToGlobal(String refNum, String locale, String siteVariant, String pageId, String instanceId, String newInstanceId) {
        try {
            logger.info("convertFuncWidgetToGlobal for {} {} {} {} {}", refNum, locale, pageId, instanceId, newInstanceId);
            String convertFuncWidgetUrl = conf.getConfig("template.api").getString("base");
            if (convertFuncWidgetUrl == null) {
                throw new PhenomException("template.api is not configured in conf file..");
            }
            convertFuncWidgetUrl = convertFuncWidgetUrl + "canvas/convertFuncWidgetToGlobal";
            Map<String, Object> params = new HashMap<>();
            params.put(REFNUM, refNum);
            params.put(LOCALE, locale);
            params.put(SITE_VARIANT, siteVariant);
            params.put(PAGE_ID, pageId);
            params.put("instanceId", instanceId);
            params.put("newInstanceId", newInstanceId);
            JsonNode response = remoteServiceCaller.sendPostSync(convertFuncWidgetUrl, params);
            logger.debug("response from convertFuncWidgetToGlobal for {} is {}", params, response);
        } catch (Exception ex) {
            logger.error("Exception in convertFuncWidgetToGlobal {}", ex);
        }
    }

    private void disconnectGlobalFuncWidget(String refNum, String locale, String siteVariant, String pageId, String instanceId, String newInstanceId) {
        try {
            logger.info("disconnectGlobalFuncWidget for {} {} {} {}", refNum, locale, instanceId, newInstanceId);
            String disconnectGlobalUrl = conf.getConfig("template.api").getString("base");
            if (disconnectGlobalUrl == null) {
                throw new PhenomException("template.api is not configured in conf file..");
            }
            disconnectGlobalUrl = disconnectGlobalUrl + "canvas/disconnectGlobalFuncWidget";
            Map<String, Object> params = new HashMap<>();
            params.put(REFNUM, refNum);
            params.put(LOCALE, locale);
            params.put(SITE_VARIANT, siteVariant);
            params.put(PAGE_ID, pageId);
            params.put("instanceId", instanceId);
            params.put("newInstanceId", newInstanceId);
            JsonNode response = remoteServiceCaller.sendPostSync(disconnectGlobalUrl, params);
            logger.debug("response from disconnectGlobalFuncWidget for {} is {}", params, response);
        } catch (Exception ex) {
            logger.error("Exception in disconnectGlobalFuncWidget {}", ex);
        }
    }

    public boolean isDisplayNamePresent(Document query, String collectionName, String fieldName, String displayName) {
        List<String> globalWidgetDisplayNames = mongoManager.getUniqueList(fieldName, query, collectionName, db);
        for (String globalWidgetDisplayName : globalWidgetDisplayNames) {
            if (displayName.equalsIgnoreCase(globalWidgetDisplayName)) {
                return true;
            }
        }
        return false;
    }

    public void removePageIdForGlobalWidgetAndCopy(String refNum, String locale, String siteVariant, String pageId, String instanceId, String newInstanceId) {
        List<String> locales = siteUtil.getTenantDetails(refNum).getSupportedLanguagesList().stream().map(lang -> lang.toLowerCase()).collect(Collectors.toList());
        Document query = new Document(REFNUM, refNum).append(LOCALE, locale).append(PERSONA, siteVariant).append(PAGE_ID, pageId).append(INSTANCE_ID_FIELD, instanceId);
        List<Document> contentDocs = mongoManager.findAllDocuments(CANVAS_SITE_CONTENT, db, query);
        mongoManager.deleteDocuments(query, CANVAS_SITE_CONTENT, db);
        for (Document contentDoc : contentDocs) {
            contentDoc.remove(ID);
            contentDoc.remove(PAGE_ID);
            contentDoc.append(GLOBAL_WIDGET, true);
            contentDoc.append(INSTANCE_ID_FIELD, newInstanceId);
        }
        if (!contentDocs.isEmpty()) {
            copyGlobalWidgetToAllLocales(locales, contentDocs);
        }
        query.remove(PERSONA);
        query.append(SITE_VARIANT, siteVariant);
        query.remove(INSTANCE_ID_FIELD);
        query.put("$or", getInnerWidgetsQuery(INSTANCE_ID_FIELD, instanceId));
        List<Document> settings = mongoManager.findAllDocuments(CANVAS_SITE_INSTANCE_SETTINGS, db, query);
        mongoManager.deleteDocuments(query, CANVAS_SITE_INSTANCE_SETTINGS, db);
        for (Document contentDoc : settings) {
            contentDoc.remove(ID);
            contentDoc.remove(PAGE_ID);
            contentDoc.append(GLOBAL_WIDGET, true);
            String innerInstanceId = contentDoc.getString(INSTANCE_ID_FIELD);
            String finalNewInstanceId = innerInstanceId.replace(innerInstanceId.split("\\$\\$")[0], newInstanceId);
            contentDoc.append(INSTANCE_ID_FIELD, finalNewInstanceId);
        }
        if (!settings.isEmpty()) {
            mongoManager.insertDocuments(settings, CANVAS_SITE_INSTANCE_SETTINGS, db);
        }
        List<Document> settingsMeta = mongoManager.findAllDocuments(CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db, query);
        mongoManager.deleteDocuments(query, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
        for (Document contentDoc : settingsMeta) {
            contentDoc.remove(ID);
            contentDoc.remove(PAGE_ID);
            contentDoc.append(GLOBAL_WIDGET, true);
            String innerInstanceId = contentDoc.getString(INSTANCE_ID_FIELD);
            String finalNewInstanceId = innerInstanceId.replace(innerInstanceId.split("\\$\\$")[0], newInstanceId);
            contentDoc.append(INSTANCE_ID_FIELD, finalNewInstanceId);
        }
        if (!settingsMeta.isEmpty()) {
            mongoManager.insertDocuments(settingsMeta, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
        }
        List<Document> caasContent = mongoManager.findAllDocuments(CANVAS_CAAS_SITE_CONTENT, db, query);
        mongoManager.deleteDocuments(query, CANVAS_CAAS_SITE_CONTENT, db);
        for (Document contentDoc : caasContent) {
            contentDoc.remove(ID);
            contentDoc.remove(PAGE_ID);
            contentDoc.append(GLOBAL_WIDGET, true);
            contentDoc.append(INSTANCE_ID_FIELD, newInstanceId);
        }
        if (!caasContent.isEmpty()) {
            copyCaasContentToAllLocales(refNum, locale, locales, caasContent);
        }
    }

    private List<Document> getInnerWidgetsQuery(String fieldName, String hfInstanceId) {
        List<Document> hfChildQuery = new ArrayList<>();
        hfChildQuery.add(new Document(fieldName, hfInstanceId));
        hfChildQuery.add(new Document(fieldName, new Document("$regex", hfInstanceId + "\\$\\$")));
        return hfChildQuery;
    }

    private void copyCaasContentToAllLocales(String refNum, String srcLocale, List<String> locales, List<Document> contentDocs) {
        for (String locale : locales) {
            contentDocs.forEach(doc -> {
                Map<String, Object> query = doc.get("query", Map.class);
                if (doc.getString("filterType").equals("static")) {
                    if (query.containsKey("selectedItems")) {
                        List<String> selectedItems = (List<String>) query.get("selectedItems");
                        List<String> newSelectedItems = new ArrayList<>();
                        selectedItems.forEach(contentId -> {
                            String cloneContentId = cloneCaasContentId(refNum, srcLocale, locale, contentId, false);
                            if (cloneContentId != null) {
                                newSelectedItems.add(cloneContentId);
                            }
                        });
                        query.put("selectedItems", newSelectedItems);
                    }
                }
                String filterId = cloneCaasFilterId(refNum, locale, "filter copy " + locale, doc.getString("contentType"), doc.getString("filterType"), query);
                doc.put("filterId", filterId);
                doc.put("query", query);
                doc.put(LOCALE, locale);
                doc.remove(ID);
            });
            mongoManager.insertDocuments(contentDocs, CANVAS_CAAS_SITE_CONTENT, db);
        }
    }

    private void copyGlobalWidgetToAllLocales(List<String> locales, List<Document> contentDocs) {
        for (String locale : locales) {
            contentDocs.forEach(document -> {
                document.remove(ID);
                document.append(LOCALE, locale);
            });
            mongoManager.insertDocuments(contentDocs, CANVAS_SITE_CONTENT, db);
        }
    }

    private void makeWidgetGlobalTrue(String refNum, String locale, String siteVariant, String pageId, String globalWidgetId, String targetId, String newInstanceId) {
        List<String> devices = Arrays.asList("desktop", "mobile");
        for (String device : devices) {
            String pageKey = SiteUtil.constructPhPageKey(refNum, device, locale, pageId);
            String pageContent = redisManager.get(pageKey);
            Page page = Json.fromJson(Json.parse(pageContent), Page.class);
            org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
            Element widget = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, targetId).first();
            if (widget != null) {
                widget.attr("global-widget", "true");
                widget.attr("global-widget-id", globalWidgetId);
                widget.attr(INSTANCE_ID_FIELD, newInstanceId);
                widget.attr(CANVAS_EDIT_ATTR, "true");
                page.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(page).toString());
            }
        }
    }

    public void copyGlobalWidgetLocaleContent(String refNum, String targetLocale, String instanceId) {
        try {
            //logger
            logger.info("copyGlobalWidgetLocaleContent for {} {} {} {}", refNum, targetLocale, instanceId);
            Document checkQuery = new Document(REFNUM, refNum).append(LOCALE, targetLocale).append(INSTANCE_ID_FIELD, instanceId).append(GLOBAL_WIDGET, true).append(DEVICE, new Document("$exists", true));
            Document deleteQuery = new Document(checkQuery);
            deleteQuery.put(DEVICE, new Document("$exists", false));
            if (mongoManager.checkIfDocumentExists(CANVAS_SITE_CONTENT, db, deleteQuery)) {
                //logger
                logger.info("Global Widget Locale Content docs without device found for refNum {} " + "and instanceId {} in locale {}, so deleting them", refNum, instanceId, targetLocale);
                mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_CONTENT, db);
            }
            if (mongoManager.checkIfDocumentExists(CANVAS_SITE_CONTENT, db, checkQuery)) {
                logger.info("Global Widget Locale Content already exists for refNum {} and instanceId {} in locale {}", refNum, instanceId, targetLocale);
                return;
            }
            Document gbQuery = new Document(REFNUM, refNum).append(INSTANCE_ID_FIELD, instanceId);
            Document universalGlobalWidgetDoc = mongoManager.findDocument(CANVAS_MIGRATED_AURELIA_GLOBAL_WIDGET, db, gbQuery);
            if (universalGlobalWidgetDoc == null) {
                logger.info("Global Widget not found for refNum {} and instanceId {}", refNum, instanceId);
                return;
            }
            String srcLocale = universalGlobalWidgetDoc.getString(LOCALE);
            Document query = new Document(REFNUM, refNum).append(LOCALE, srcLocale).append(INSTANCE_ID_FIELD, instanceId).append(GLOBAL_WIDGET, true);
            List<Document> contentDocs = mongoManager.findAllDocuments(CANVAS_SITE_CONTENT, db, query);
            for (Document contentDoc : contentDocs) {
                contentDoc.remove(ID);
                contentDoc.append(LOCALE, targetLocale);
            }
            if (!contentDocs.isEmpty()) {
                mongoManager.insertDocuments(contentDocs, CANVAS_SITE_CONTENT, db);
            } else {
                //logger
                logger.info("No Global Widget Locale Content found for refNum {} and instanceId {} in src locale {}", refNum, instanceId, srcLocale);
            }
        } catch (Exception e) {
            //error
            logger.error("Exception in copyGlobalWidgetLocaleContent widgetDoc {} : ", e);
        }
    }

    public void generateContentData(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device, Element widgetEle, boolean isSaved) {
        int cardsCount = 0;
        List<Document> contentDocs = new ArrayList<>();
        List<String> cardIds = new ArrayList<>();
        Elements repeaterElements = widgetEle.getElementsByAttribute(CANVAS_REPEATABLE);
        if (repeaterElements != null && !repeaterElements.isEmpty()) {
            Element repeaterElement = repeaterElements.first();
            cardIds = getDataPsIdsFromElement(repeaterElement);
        }
        List<String> finalCardIds = cardIds;
        widgetEle.getElementsByAttribute(DATA_PS).forEach(ele -> {
            if (isValidEle(ele) && !finalCardIds.contains(ele.attr(DATA_PS))) {
                contentDocs.addAll(prepareContentDocs(refNum, locale, siteVariant, pageId, instanceId, device, WIDGET, 0, ele, widgetEle.attr(CANVAS_STATIC_WIDGET_ATTR)));
            }
        });
        if (repeaterElements != null && !repeaterElements.isEmpty()) {
            Element repeaterElement = repeaterElements.first();
            Elements repeaterSiblings = repeaterElement.siblingElements();
            List<String> dataPsIds = getDataPsIdsFromElement(repeaterElement);
            cardsCount++;
            for (String dataPsId : dataPsIds) {
                Element child = repeaterElement.getElementsByAttributeValue("data-ps", dataPsId).first();
                if (child != null && isValidEle(child)) {
                    contentDocs.addAll(prepareContentDocs(refNum, locale, siteVariant, pageId, instanceId, device, CARD, 0, child, widgetEle.attr(CANVAS_STATIC_WIDGET_ATTR)));
                }
            }
            for (Element repeaterSibling : repeaterSiblings) {
                if (!repeaterSibling.hasAttr(CANVAS_REPEATABLE)) {
                    List<String> siblingIds = getDataPsIdsFromElement(repeaterSibling);
                    if (dataPsIds.size() == siblingIds.size()) {
                        siblingIds.removeAll(dataPsIds);
                        if (siblingIds.isEmpty()) {
                            cardsCount++;
                            repeaterSibling.attr(CANVAS_REPEATABLE, "");
                            for (String dataPsId : dataPsIds) {
                                Element child = repeaterSibling.getElementsByAttributeValue("data-ps", dataPsId).first();
                                if (child != null && isValidEle(child)) {
                                    contentDocs.addAll(prepareContentDocs(refNum, locale, siteVariant, pageId, instanceId, device, CARD, repeaterSiblings.indexOf(repeaterSibling) + 1, child, widgetEle.attr(CANVAS_STATIC_WIDGET_ATTR)));
                                }
                            }
                        }
                    }
                }
            }
        }
        List<Map<String, Object>> cardsData = new ArrayList<>();
        for (int i = cardsCount; i > 0; i--) {
            cardsData.add(new HashMap<>());
        }
        Document styleDoc = new Document(REFNUM, refNum);
        styleDoc.append(LOCALE, locale);
        styleDoc.append(SITE_VARIANT, siteVariant);
        styleDoc.append(DATA.DEVICE, DESKTOP);
        styleDoc.append(PAGE_ID, pageId);
        styleDoc.append(INSTANCE_ID_FIELD, instanceId);
        if (cardsCount > 0) {
            styleDoc.append("settings", ImmutableMap.of("cards", cardsData));
            Set<String> styleIdsSet = new HashSet<>();
            widgetUtil.extractStyleIds(ImmutableMap.of("cards", cardsData), styleIdsSet);
            styleDoc.append(STYLE_IDS, styleIdsSet);
        }
        if (!isSaved) {
            mongoManager.insertDocument(styleDoc, CANVAS_SITE_INSTANCE_SETTINGS, db);
        }
        mongoManager.insertDocuments(contentDocs, CANVAS_SITE_CONTENT, db);
    }

    public List<Document> prepareContentDocs(String refNum, String locale, String persona, String pageId, String instanceId, String device, String containerType, int containerIndex, Element ele, String widgetId) {
        String tagName = ele.tagName();
        if (ele.hasAttr(VIDEO_TAG_IDENTIFIER)) {
            tagName = VIDEO_TAG_IDENTIFIER;
        } else if (ele.hasAttr(EMBEDDED_CODE_IDENTIFIER)) {
            tagName = EMBEDDED_CODE_IDENTIFIER;
        }
        List<Document> contentDocs = new ArrayList<>();
        Document baseDoc = new Document(REFNUM, refNum);
        baseDoc.append(LOCALE, locale);
        baseDoc.append(PERSONA, persona);
        baseDoc.append(PAGE_ID, pageId);
        baseDoc.append(INSTANCE_ID_FIELD, instanceId);
        baseDoc.append(DATA.DEVICE, DESKTOP);
        baseDoc.append(CONTAINER_TYPE, containerType);
        baseDoc.append(INDEX, containerIndex);
        baseDoc.append(DATA_PS, ele.attr(DATA_PS));
        baseDoc.append(NODE, tagName);
        baseDoc.append(WIDGET_ID_FIELD, widgetId);
        if (ele.tagName().equalsIgnoreCase("img") || ele.tagName().equalsIgnoreCase("video")) {
            contentDocs.add(addContentAttributes(baseDoc, "src", ele.hasAttr("src") && ele.attr("src") != null ? ele.attr("src") : "", device));
            contentDocs.add(addContentAttributes(baseDoc, "alt", ele.hasAttr("alt") && ele.attr("alt") != null ? ele.attr("alt") : "", device));
        } else if (ele.tagName().equalsIgnoreCase("a")) {
            contentDocs.add(addContentAttributes(baseDoc, HREF, ele.hasAttr(HREF) && ele.attr(HREF) != null ? ele.attr(HREF) : "", device));
            contentDocs.add(addContentAttributes(baseDoc, "target", ele.hasAttr("target") && ele.attr("target") != null ? ele.attr("target") : "", device));
            contentDocs.add(addContentAttributes(baseDoc, "aria-label", ele.attr("aria-label") != null ? ele.attr("aria-label") : "", device));
            contentDocs.add(addContentAttributes(baseDoc, "title", ele.attr("title") != null ? ele.attr("title") : "", device));
            //            List<Node> childNodes = ele.childNodes();
            //            if (childNodes.size() == 1 && (childNodes.stream().findFirst().get() instanceof TextNode)) {
            //                contentDocs.add(addContentAttributes(baseDoc, "value", ele.html(), device));
            //            }
        } else if (ele.tagName().equalsIgnoreCase("use")) {
            contentDocs.add(addContentAttributes(baseDoc, HREF, ele.hasAttr(HREF) && ele.attr(HREF) != null ? ele.attr(HREF) : "", device));
        } else if (ele.hasAttr(VIDEO_TAG_IDENTIFIER)) {
            contentDocs.add(addContentAttributes(baseDoc, VIDEO_TAG_IDENTIFIER, ele.hasAttr(VIDEO_TAG_IDENTIFIER) && ele.attr(VIDEO_TAG_IDENTIFIER) != null ? ele.attr(VIDEO_TAG_IDENTIFIER) : "", device));
            contentDocs.add(addContentAttributes(baseDoc, VIDEO_OPTIONS, ele.hasAttr(VIDEO_OPTIONS) && ele.attr(VIDEO_OPTIONS) != null ? ele.attr(VIDEO_OPTIONS) : "", device));
        } else if (canAddValue(ele)) {
            contentDocs.add(addContentAttributes(baseDoc, VALUE, ele.html(), device));
        }
        if (ele.hasAttr(EDIT_ATTRIBUTE_IDENTIFIER)) {
            String attributeValue = ele.attr(EDIT_ATTRIBUTE_IDENTIFIER);
            String[] valuesArray = attributeValue.split(",");
            List<String> attrList = Arrays.asList(valuesArray);
            for (String attribute : attrList) {
                attribute = attribute.trim();
                contentDocs.add(addContentAttributes(baseDoc, attribute, ele.hasAttr(attribute) && ele.attr(attribute) != null ? ele.attr(attribute) : "", device));
            }
        }
        return contentDocs;
    }

    public boolean canAddValue(Element ele) {
        if (ele.hasAttr(EMBEDDED_CODE_IDENTIFIER)) {
            return true;
        }
        List<Node> childNodes = ele.childNodes();
        return (childNodes.size() == 1 && (childNodes.stream().findFirst().get() instanceof TextNode));
    }

    public Document addContentAttributes(Document baseDoc, String contentKey, String content, String device) {
        Document finalDoc = new Document(baseDoc);
        content = content.replaceAll("[\\n\\r]", "");
        finalDoc.append(CONTENT_KEY, contentKey);
        finalDoc.append(TAG_CONTENT, content.trim());
        if (!device.equalsIgnoreCase(DESKTOP)) {
            String deviceKey = getDeviceSpecificContentKey(device);
            finalDoc.append(deviceKey, content.trim());
        }
        return finalDoc;
    }

    public Map<String, Object> getViewMetaData(String refNum, String widgetId) {
        Document query = new Document(REFNUM, refNum);
        query.append(WIDGET_ID_FIELD, widgetId);
        Document widgetDoc = mongoManager.findDocument(CANVAS_SITEWIDGETS, db, query);
        String viewsCollection = CANVAS_SITEWIDGETVIEWS;
        if (widgetDoc == null) {
            query.remove(REFNUM);
            viewsCollection = CANVAS_GLOBALWIDGETVIEWS;
            query.append(LATEST, true);
            widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, query);
            if (widgetDoc == null) {
                logger.info("widget doc not found for {} {}", refNum, widgetId);
                return new HashMap<>();
            }
        }
        query.remove(WIDGET_ID_FIELD);
        query.append(VIEW_ID, widgetDoc.getString(VIEW_ID));
        Document viewDoc;
        if (viewsCollection.equalsIgnoreCase(CANVAS_GLOBALWIDGETVIEWS)) {
            viewDoc = preProdMongoManager.findDocument(viewsCollection, preprodDb, query);
        } else {
            viewDoc = mongoManager.findDocument(viewsCollection, db, query);
        }
        if (viewDoc != null && viewDoc.containsKey("metaData")) {
            return (Map<String, Object>) viewDoc.get("metaData");
        }
        return new HashMap<>();
    }

    public Map<String, String> getLinkTextMetadata(String refNum, String widgetId) {
        Map<String, String> linkTextMetaData = new HashMap<>();
        Document query = new Document(REFNUM, refNum);
        query.append(WIDGET_ID_FIELD, widgetId).append(LATEST, true);
        Document widgetDoc = mongoManager.findDocument(CANVAS_SITEWIDGETS, db, query);
        String viewsCollection = CANVAS_SITEWIDGETVIEWS;
        if (widgetDoc == null) {
            query.remove(REFNUM);
            viewsCollection = CANVAS_GLOBALWIDGETVIEWS;
            widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, query);
            if (widgetDoc == null) {
                logger.info("widget doc not found for {} {}", refNum, widgetId);
                return linkTextMetaData;
            }
        }
        query.remove(WIDGET_ID_FIELD);
        query.append(VIEW_ID, widgetDoc.getString(VIEW_ID));
        query.append(LATEST, true);
        Document viewDoc;
        if (viewsCollection.equalsIgnoreCase(CANVAS_GLOBALWIDGETVIEWS)) {
            viewDoc = preProdMongoManager.findDocument(viewsCollection, preprodDb, query);
        } else {
            viewDoc = mongoManager.findDocument(viewsCollection, db, query);
        }
        if (viewDoc != null) {
            String html = viewDoc.getString("viewHtml");
            org.jsoup.nodes.Document htmlDoc = HtmlParser.parse(html);
            Elements anchorElements = htmlDoc.getElementsByTag("a");
            if (!anchorElements.isEmpty()) {
                for (Element anchorElement : anchorElements) {
                    Elements textElements = anchorElement.getElementsByTag("span");
                    for (Element textElement : textElements) {
                        if (textElement.classNames().contains("phw-actionlink-text")) {
                            linkTextMetaData.put(anchorElement.attr(DATA_PS), textElement.attr(DATA_PS));
                        }
                    }
                }
            }
        }
        return linkTextMetaData;
    }

    public String getCanvasRenderedHtml(String refNum, String locale, String siteVariant, String device, String pageId, String instanceId, boolean isAureliaMigrated, String pageHtml) {
        try {
            String url = conf.getString("url.mashup.enhanceWidget");
            if (url == null) {
                throw new PhenomException("url.mashup.enhanceWidget is not configured in conf file..");
            }
            Map<String, Object> payLoad = new HashMap<>();
            payLoad.put(REFNUM, refNum);
            payLoad.put(LOCALE, locale);
            payLoad.put(SITE_VARIANT, siteVariant);
            payLoad.put(PAGE_ID, pageId);
            payLoad.put("instance-id", instanceId);
            payLoad.put("device", device);
            payLoad.put("isAureliaMigrated", isAureliaMigrated);
            payLoad.put("pageHtml", pageHtml);
            JsonNode response = remoteServiceCaller.sendPostSync(url, payLoad);
            logger.info(" Response from mashup service is : {}", response);
            if (response != null && response.has("status") && (response.get("status").asText()).equalsIgnoreCase(SUCCESS)) {
                if (response.get("data") != null) {
                    String html = response.get("data").asText();
                    String innerHtml = Jsoup.parse(html).getElementsByTag("body").first().child(0).html();
                    return innerHtml;
                }
            } else {
                logger.error(" Error from mashup service : {}", response);
                return null;
            }
        } catch (Exception ex) {
            return null;
        }
        return null;
    }

    public Document getDefaultTagSettingOptions() {
        Document designSettingsOptionsDoc = mongoManager.findDocument("canvas_tag_default_settings", db, new Document());
        if (designSettingsOptionsDoc != null) {
            designSettingsOptionsDoc.remove("_id");
        }
        return designSettingsOptionsDoc;
    }

    public Map<String, Object> getMasterThemeVariables() {
        Map<String, Object> masterVariablesData = new HashMap<>();
        Document queryDoc = new Document(REFNUM, CANVUS_REFNUM);
        Document masterVariablesDoc = mongoManager.findDocument("canvas_master_theme_variables", db, queryDoc);
        if (masterVariablesDoc != null) {
            masterVariablesData = (Map<String, Object>) masterVariablesDoc.get("themeVariables");
        }
        return masterVariablesData;
    }

    public boolean isValidEle(Element ele) {
        List<Node> childNodes = ele.childNodes();
        return (childNodes.size() == 1 && (childNodes.stream().findFirst().get() instanceof TextNode)) || ele.tagName().equalsIgnoreCase("img") || ele.tagName().equalsIgnoreCase("use") || ele.tagName().equalsIgnoreCase("a") || ele.hasAttr(VIDEO_TAG_IDENTIFIER) || ele.hasAttr(EMBEDDED_CODE_IDENTIFIER) || ele.hasAttr(EDIT_ATTRIBUTE_IDENTIFIER);
    }

    public Document updateStaticElementContent(String refNum, String locale, String persona, String pageId, String instanceId, String dataPs, String containerType, int index, String value, String contentKey, boolean globalWidget, String device, boolean isAutomationScreen, boolean aureliaWidget, String hfType, String syncedFromGlobal, String isCaas) {
        Document activityResp = new Document();
        Document query = new Document(REFNUM, refNum);
        query.append(LOCALE, locale);
        if (!globalWidget) {
            query.append(PERSONA, persona);
            query.append(PAGE_ID, pageId);
        } else {
            query.append(GLOBAL_WIDGET, true);
        }
        query.append(INSTANCE_ID_FIELD, instanceId);
        query.append(DATA_PS, dataPs);
        query.append(CONTAINER_TYPE, containerType);
        query.append(INDEX, index);
        query.append(CONTENT_KEY, VALUE);
        if (contentKey != null) {
            query.append(CONTENT_KEY, contentKey);
        }
        Document updateDoc;
        if (!DESKTOP.equalsIgnoreCase(device)) {
            String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
            if (syncedFromGlobal != null) {
                logger.debug("syncedFromGlobal exists for for refNum {} locale {} dataPs {} pageId {} instanceId {}", refNum, locale, dataPs, pageId, instanceId);
                updateDoc = new Document("$set", new Document(deviceSpecificContentKey, value).append("syncedFromGlobal", Boolean.valueOf(syncedFromGlobal)));
            } else {
                updateDoc = new Document("$set", new Document(deviceSpecificContentKey, value));
            }
        } else {
            if (syncedFromGlobal != null) {
                logger.debug("syncedFromGlobal exists for refNum {} locale {} dataPs {} pageId {} instanceId {}", refNum, locale, dataPs, pageId, instanceId);
                updateDoc = new Document("$set", new Document(TAG_CONTENT, value).append("syncedFromGlobal", Boolean.valueOf(syncedFromGlobal)));
            } else {
                updateDoc = new Document("$set", new Document(TAG_CONTENT, value));
            }
        }
        if (isCaas != null) {
            updateDoc.get("$set", Document.class).put("isCaas", Boolean.valueOf(isCaas));
        }
        try {
            activityResp.put("previousValue", mongoManager.findDocument(CANVAS_SITE_CONTENT, db, query));
        } catch (Exception e) {
            logger.error("exception in previous value reading {} {}", e, query);
        }
        mongoManager.upsert(query, updateDoc, Constants.CANVAS_SITE_CONTENT, db);
        if (!isAutomationScreen) {
            addOrRemoveDeviceOverriddenAttr(refNum, locale, pageId, instanceId, device, "add");
            makeWidgetInstanceDirty(refNum, locale, pageId, instanceId);
        }
        if (instanceId.startsWith("hf-")) {
            setPageHFHasEditTrue(refNum, locale, instanceId);
            updateHFPublishStatus(refNum, locale, instanceId);
        }
        if (globalWidget) {
            setGlobalWidgetHasEditTrue(refNum, locale, instanceId);
        }
        if (aureliaWidget && hfType != null) {
            updateHasEditForNonCaasHeaderFooter(refNum, locale, pageId, hfType);
        }
        return activityResp;
    }

    public boolean isCanvasSite(String refNum, Optional<String> siteVariant) {
        try {
            if (siteVariant.isPresent()) {
                logger.info("isCanvasSite check for {}, siteVariant : {}", refNum, siteVariant);
            } else {
                logger.info("isCanvasSite check for {}", refNum);
            }
            if (refNum.equalsIgnoreCase(MASTER_TEMPLATE_REFNUM)) {
                return true;
            }
            TenantDetails td = getTenantDetails(refNum);
            if (td != null) {
                if (td.isCanvasSite()) {
                    if (siteVariant.isPresent() && siteVariant.get().equals(INTERNAL_SITE_TYPE) && td.getSiteVariants() != null && !td.getSiteVariants().isEmpty() && td.getSiteVariants().containsKey(INTERNAL_SITE_TYPE)) {
                        return false;
                    } else {
                        return true;
                    }
                }
                /*if (td.getSourceRefNum() != null) {
                    TenantDetails sourceRefNum = getTenantDetails(td.getSourceRefNum());
                    if (sourceRefNum.isCanvasSite()) {
                        return true;
                    }
                }*/
            }
            return false;
        } catch (Exception e) {
            logger.error("exception in isCanvasSite for {}", refNum, e);
        }
        return false;
    }

    public boolean isCanvasMigratedSite(String refNum, Optional<String> siteVariant) {
        try {
            if (siteVariant.isPresent()) {
                logger.info("isCanvasMigratedSite check for {}, siteVariant : {}", refNum, siteVariant);
            } else {
                logger.info("isCanvasMigratedSite check for {}", refNum);
            }
            TenantDetails td = getTenantDetails(refNum);
            if (td != null) {
                if (td.isCanvasSite() && td.isMigratedSite()) {
                    if (siteVariant.isPresent() && siteVariant.get().equals(INTERNAL_SITE_TYPE) && td.getSiteVariants() != null && !td.getSiteVariants().isEmpty() && td.getSiteVariants().containsKey(INTERNAL_SITE_TYPE)) {
                        return false;
                    } else {
                        return true;
                    }
                }
            }
            return false;
        } catch (Exception e) {
            logger.error("exception in isCanvasMigratedSite for {}", refNum, e);
        }
        return false;
    }

    public TenantDetails getTenantDetails(String refNum) {
        String key = RedisKeyUtil.getTenantDetailsKey(refNum);
        logger.debug("Tenant Details Key :: " + key);
        String tenantDetailsStr = redisManager.get(key);
        JsonNode tenantDetailsJson = Json.parse(tenantDetailsStr);
        TenantDetails td = Json.fromJson(tenantDetailsJson, TenantDetails.class);
        return td;
    }

    public Map<String, Object> bulkUpdateContent(String refNum, String locale, String persona, String pageId, String device, String instanceId, boolean globalWidget, List<Map<String, Object>> contentMap) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        try {
            ExecutorService executorService = Executors.newFixedThreadPool(10);
            List<CompletableFuture<Void>> completableFutures = new ArrayList<>();
            for (Map<String, Object> content : contentMap) {
                String dataPs = content.get("dataPs").toString();
                String containerType = content.get(CONTAINER_TYPE).toString();
                int index = (int) content.get(INDEX);
                String value = content.get(VALUE).toString();
                completableFutures.add(CompletableFuture.runAsync(() -> {
                    updateStaticElementContent(refNum, locale, persona, pageId, instanceId, dataPs, containerType, index, value, null, globalWidget, device, false, false, null, null, null);
                }, executorService));
            }
            //Restricting threads to maximum of 10
            CompletableFuture<Void> allFutures = CompletableFuture.allOf(completableFutures.toArray(new CompletableFuture[0]));
            allFutures.join();
            executorService.shutdown();
        } catch (Exception ex) {
            logger.error("Exception in bulkUpdateContent", ex);
            response.put(STATUS_KEY, false);
        }
        return response;
    }

    public String addCustomStylesCssToPage(String refNum, String theme) {
        List<String> devicesList = new ArrayList<>();
        devicesList.add("desktop");
        devicesList.add("largeDesktop");
        devicesList.add("tab");
        devicesList.add("mobile");
        List<String> states = Arrays.asList("normal", "hover", "focus", "active", "disabled", "visited", "focus-visible", "otherStates");
        Map<String, String> dynamicPageTagVsClasses = new HashMap<>();
        dynamicPageTagVsClasses.put("jdstyles", "phw-job-description ");
        dynamicPageTagVsClasses.put("blogPagestyles", "phw-blog-description ");
        dynamicPageTagVsClasses.put("eventPagestyles", "phw-event-description ");
        String orRegex = "";
        Document jdStylesQuery = new Document(REFNUM, refNum);
        jdStylesQuery.put(THEME_ID, theme);
        jdStylesQuery.put("feature", "jdstyles");
        jdStylesQuery.put("enabled", false);
        boolean jdStylesEnabled = !mongoManager.checkIfDocumentExists("site_feature_flags", conf.getString(MONGO_DB), jdStylesQuery);
        if (!jdStylesEnabled) {
            orRegex = orRegex + dynamicPageTagVsClasses.get("jdstyles");
        }
        jdStylesQuery.put("feature", "blogPagestyles");
        boolean blogStylesEnabled = !mongoManager.checkIfDocumentExists("site_feature_flags", conf.getString(MONGO_DB), jdStylesQuery);
        if (!blogStylesEnabled) {
            orRegex = orRegex + ((orRegex.isEmpty() || orRegex.endsWith("|")) ? "" : "|") + dynamicPageTagVsClasses.get("blogPagestyles");
        }
        jdStylesQuery.put("feature", "eventPagestyles");
        boolean eventStylesEnabled = !mongoManager.checkIfDocumentExists("site_feature_flags", conf.getString(MONGO_DB), jdStylesQuery);
        if (!eventStylesEnabled) {
            orRegex = orRegex + ((orRegex.isEmpty() || orRegex.endsWith("|")) ? "" : "|") + dynamicPageTagVsClasses.get("eventPagestyles");
        }
        String fileName = "canvas-site-custom-styles-" + System.currentTimeMillis();
        String localFilePath = "/tmp/" + fileName + ".css";
        Path path = Paths.get(localFilePath);
        long totalDocsLength = mongoManager.findDocumentCount(CANVAS_SITE_CUSTOM_STYLES, conf.getString(CONFIG.MONGO_DB), new Document(REFNUM, refNum).append(THEME, theme));
        long totaDocsAfterloop = 0;
        for (String device : devicesList) {
            for (String state : states) {
                StringBuffer staticWidgetCss = new StringBuffer();
                org.bson.Document query = new org.bson.Document(DATA.REFNUM, refNum).append(THEME, theme).append(DEVICE_TYPE, device);
                if (state.equalsIgnoreCase("normal")) {
                    query.put("state", new Document("$exists", false));
                } else if (state.equalsIgnoreCase("otherStates")) {
                    query.put("$and", Arrays.asList(new Document("state", new Document("$nin", states)), new Document("state", new Document("$exists", true))));
                } else {
                    query.put("state", state);
                }
                Document styleQuery = new Document(query);
                styleQuery.put(STYLE_ID, DEFAULT);
                List<org.bson.Document> defaultStyleDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, conf.getString(CONFIG.MONGO_DB), styleQuery, Arrays.asList(CSS));
                if (defaultStyleDocs != null && !defaultStyleDocs.isEmpty()) {
                    defaultStyleDocs.stream().forEach(doc -> staticWidgetCss.append(doc.getString("css")).append("\n"));
                    totaDocsAfterloop += defaultStyleDocs.size();
                }
                styleQuery.put(STYLE_ID, new Document("$ne", DEFAULT));
                if (!(jdStylesEnabled && blogStylesEnabled && eventStylesEnabled)) {
                    jdStylesQuery = new Document(styleQuery);
                    jdStylesQuery.put(STYLE_ID, new Document("$not", new Document("$regex", orRegex)));
                } else {
                    jdStylesQuery = new Document(styleQuery);
                }
                int batchSize = conf.getInt(CONFIG.CANVAS_CUST_STYLE_BATCH_SIZE, DATA.CANVAS_CUST_STYLE_BATCH_SIZE_VALUE);
                int skip = 0;
                List<org.bson.Document> styleDocs;
                try (BufferedWriter writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8, Files.exists(path) ? StandardOpenOption.APPEND : StandardOpenOption.CREATE)) {
                    do {
                        styleDocs = mongoManager.findAllDocumentsInBatch(CANVAS_SITE_CUSTOM_STYLES, conf.getString(CONFIG.MONGO_DB), new Document("$and", Arrays.asList(styleQuery, jdStylesQuery)), Arrays.asList(CSS), skip, batchSize, new Document(ID, -1));
                        if (styleDocs != null && !styleDocs.isEmpty()) {
                            if (skip == 0) {
                                writer.write(staticWidgetCss.toString());
                                writer.newLine();
                            }
                            for (org.bson.Document doc : styleDocs) {
                                writer.write(doc.getString("css"));
                                writer.newLine();
                            }
                            totaDocsAfterloop += styleDocs.size();
                            skip += batchSize;
                        } else {
                            break;
                        }
                    } while (styleDocs.size() >= batchSize);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
                logger.info("CSS file written to: {} for device: {}", localFilePath, device);
            }
        }
        logger.info("totalDocsLength -->{}, totaDocsAfterloop -->{}", totalDocsLength, totaDocsAfterloop);
        File cssFile = null;
        String filePath = conf.getString(CONFIG.S3_ROOT) + "/" + refNum + "/canvas/assets/css/" + theme + "/";
        Document cssQuery = new Document(REFNUM, refNum).append(THEME, theme);
        String oldFile = "";
        try {
            /*
                TODO: delete all the previous files
                TODO: generate single file for all
                 */
            //                cssFile = new File("page-canvas-static-widgets-" + System.currentTimeMillis() + ".css");
            //            oldFile = deleteOldFile(CANVAS_SITE_CSS_URL, cssQuery, CSS_URL, filePath, "canvas-site-custom-styles");
            //            InputStream inputStream = new ByteArrayInputStream(staticWidgetCss.toString()
            //            .getBytes(StandardCharsets.UTF_8));
            try (InputStream inputStream = Files.newInputStream(path)) {
                String themeCss = filePath + fileName + ".css";
                logger.info("Uploading CSS to path: {}", themeCss);
                S3ObjectFileMetaData metaData = new S3ObjectFileMetaData();
                metaData.setContentType("text/css");
                metaData.setContentLength(Files.size(path));
                storageManager.uploadStreamWithMetaData(new BufferedInputStream(inputStream), themeCss, metaData);
            }
        } catch (Exception e) {
            logger.error("Failed uploading site css", e);
            return null;
        } finally {
            try {
                Files.delete(path);
            } catch (IOException e) {
                logger.error("Failed deleting local file", e);
            }
        }
        String cdnUrl = conf.getString(CONFIG.CDN_URL) + "/" + refNum + "/canvas/assets/css/" + theme + "/" + fileName + ".css";
        Document update = new Document(CSS_URL, cdnUrl);
        Document updateDoc = new Document("$set", update);
        mongoManager.upsert(cssQuery, updateDoc, CANVAS_SITE_CSS_URL, conf.getString(CONFIG.MONGO_DB));
        return cdnUrl;
    }

    public Map<String, Object> restoreWidget(CanvasRestoreWidgetRequest canvasRestoreWidgetRequest, Http.Request request) {
        try {
            Map<String, Object> response = new HashMap<>();
            logger.info("Restoring deleted widget");
            //            Element savedWidgetEle = widgetUtil.getCanvasWidget(savedStructure);
            /*String widgetType = canvasRestoreWidgetRequest.getType();*/
            String widgetId = canvasRestoreWidgetRequest.getWidgetId();
            String instanceId = canvasRestoreWidgetRequest.getInstanceId();
            String refNum = canvasRestoreWidgetRequest.getRefNum();
            String locale = canvasRestoreWidgetRequest.getLocale();
            String device = canvasRestoreWidgetRequest.getTargetDevice();
            String pageId = canvasRestoreWidgetRequest.getPageId();
            Document savQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(WIDGET_ID_FIELD, widgetId).append(INSTANCE_ID_FIELD, instanceId).append(PAGE_ID, pageId);
            if (device != null && !device.isEmpty()) {
                savQuery.append("targetDevice", device);
            } else {
                savQuery.append("targetDevice", new Document("$exists", false));
            }
            org.bson.Document widgetDoc = mongoManager.findDocument(CANVAS_DELETED_WIDGETS, db, savQuery);
            if (widgetDoc == null) {
                response.put(STATUS_KEY, false);
                response.put("message", "widget doc not found for " + widgetId);
                return response;
            }
            CanvasDragDropRequest canvasRestoreDragDropRequest = Json.fromJson(Json.toJson(widgetDoc), CanvasDragDropRequest.class);
            String widgetStructure = widgetDoc.getString("sectionHtml");
            Element pageEle = widgetUtil.getCanvasWidget(widgetStructure);
            if (widgetDoc.containsKey(AURELIA_MIGRATED_WIDGET_TEXT) && Boolean.TRUE.equals(widgetDoc.getBoolean(AURELIA_MIGRATED_WIDGET_TEXT))) {
                String mobileWidgetStructure = widgetDoc.getString("sectionHtmlMobile");
                Element mobilePageEle = widgetUtil.getCanvasWidget(mobileWidgetStructure);
                placeRestoredWidgetInPage(canvasRestoreDragDropRequest, pageEle, mobilePageEle, true);
            } else {
                placeRestoredWidgetInPage(canvasRestoreDragDropRequest, pageEle, null, false);
            }
            mongoManager.deleteDocument(Constants.CANVAS_DELETED_WIDGETS, db, savQuery);
            if (widgetDoc.containsKey("settings") && widgetDoc.get("settings") != null) {
                generateSettingsMetadata(canvasRestoreWidgetRequest.getRefNum(), canvasRestoreWidgetRequest.getLocale(), widgetDoc.getString(SITE_VARIANT), canvasRestoreWidgetRequest.getPageId(), instanceId, canvasRestoreWidgetRequest.getTargetDevice(), (Map<String, Object>) widgetDoc.get("settings", Map.class));
            }
            response.put(STATUS_KEY, true);
            if (!pageId.startsWith(BLOGARTICLE_IDENTIFIER)) {
                siteUtil.canvasApplyToLowerEnvByEndpoint(Json.fromJson(Json.toJson(canvasRestoreWidgetRequest), Map.class), "widget/restoreWidget", request);
            }
            return response;
        } catch (Exception ex) {
            logger.error("Exception in addSavedWidget {}", ex);
            return null;
        }
    }

    public Map<String, Object> reorderWidgets(String refNum, String locale, String siteVariant, String pageId, String deviceMode, Map<String, List<Object>> widgetsList, List<String> activityDevices) {
        Map<String, Object> response = new HashMap<>();
        response.put(STATUS_KEY, true);
        try {
            List<String> devices = new ArrayList<>();
            if (deviceMode.equalsIgnoreCase("desktop")) {
                devices.add("desktop");
                devices.add("mobile");
            } else {
                devices.add("mobile");
            }
            for (String device : devices) {
                String pageKey = "ph:page:" + refNum + ":" + device + ":" + locale + ":" + pageId;
                String pageValue = redisManager.get(pageKey);
                if (pageValue != null) {
                    Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                    if (deviceMode.equalsIgnoreCase("desktop") && device.equalsIgnoreCase("mobile") && p.isWidgetRearrangementDisconnected()) {
                        logger.info("Since there is Mobile specific Rearrangement done already, skipping rearrangement in " + "Mobile page..");
                        continue;
                    }
                    activityDevices.add(device);
                    String pageHtml = p.getPageHtml();
                    org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageHtml);
                    Element phPage = pageDoc.getElementsByClass("ph-page").first();
                    for (String parentId : widgetsList.keySet()) {
                        Element parent = phPage.getElementsByAttributeValue("data-ph-id", parentId).first();
                        if (parent != null) {
                            processReorder(parent, widgetsList.get(parentId));
                        }
                    }
                    if (deviceMode.equalsIgnoreCase("mobile")) {
                        p.setWidgetRearrangementDisconnected(true);
                    }
                    p.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(p).toString());
                }
            }
        } catch (Exception ex) {
            logger.error("Exception in reorderWidgets {}", ex);
            response.put(STATUS_KEY, false);
        }
        return response;
    }

    public void processReorder(Element parent, List<Object> widgetList) {
        for (int i = 0; i < widgetList.size(); i++) {
            Map<String, String> widgetMap = (Map<String, String>) widgetList.get(i);
            logger.info("Processing reorder {} {}", i, widgetMap.get("widgetId"));
            Elements existingEle = parent.getElementsByAttributeValue("data-ph-id", widgetMap.get("targetId"));
            if (existingEle != null && !existingEle.isEmpty()) {
                parent.insertChildren(i, existingEle);
            } else {
                logger.info("not found ele with data-ph-id {}", widgetMap.get("targetId"));
            }
        }
    }

    public Map<String, Object> getStyleAttributeUsage(String refNum, String variableName, String type, String theme) {
        /*Map<String, Map<String, Object>> localeVsPageIdDocs = new HashMap<>();*/
        Map<String, Map<String, Object>> responseStructure = new HashMap<>();
        Map<String, Object> result = new HashMap<>();
        Set<String> instanceIds = new HashSet<>();
        int count = 0;
        if (type.equalsIgnoreCase("style")) {
            /*getStyleIdUsage(refNum, variableName, localeVsPageIdDocs, instanceIds);*/
            getStyleIdUsageV3(refNum, variableName, responseStructure, instanceIds, theme);
        } else if (type.equalsIgnoreCase("font-preset")) {
            Document presetQuery = new Document(REFNUM, refNum);
            presetQuery.append(THEME, theme);
            presetQuery.append("fontPresetId", variableName);
            List<String> projections = Arrays.asList(STYLE_ID);
            Set<String> styleIds = new HashSet<>();
            mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, presetQuery, projections).stream().forEach(document -> styleIds.add(document.getString(STYLE_ID)));
            styleIds.forEach(styleId -> getStyleIdUsageV3(refNum, styleId, responseStructure, instanceIds, theme));
        } else {
            String collection = "canvas_site_type_metadata".replace("type", type);
            Document query = new Document(REFNUM, refNum).append("variable_name", variableName);
            List<String> projections = Arrays.asList(STYLE_ID);
            Set<String> styleIds = new HashSet<>();
            mongoManager.findAllDocuments(collection, db, query, projections).stream().forEach(document -> styleIds.add(document.getString(STYLE_ID)));
            styleIds.forEach(styleId -> getStyleIdUsageV3(refNum, styleId, responseStructure, instanceIds, theme));
        }
        for (Map.Entry<String, Map<String, Object>> entry : responseStructure.entrySet()) {
            int localeCount = 0;
            Map<String, Object> variantMap = entry.getValue();
            for (Map.Entry<String, Object> innerEntry : variantMap.entrySet()) {
                int innerCount = ((Set) innerEntry.getValue()).size();
                localeCount += innerCount;
            }
            entry.getValue().put("count", localeCount);
            count += localeCount;
        }
        result.put("pages_count", count);
        result.put("count", instanceIds.size());
        result.put("data", responseStructure);
        return result;
    }

    public Map<String, Object> getTemplateCompanyName(String refNum) {
        Map<String, Object> result = new HashMap<>();
        logger.info("fetching sourceRefNum from tenantDetails using refNum ", refNum);
        String sourceRefNum = siteUtil.getTenantDetails(refNum).getSourceRefNum();
        if (sourceRefNum != null) {
            logger.info("fetching companyName from tenantDetails using refNum", sourceRefNum);
            String companyName = siteUtil.getTenantDetails(sourceRefNum).getCompanyName();
            result.put("sourceRefNum", sourceRefNum);
            result.put("companyName", companyName);
        } else {
            logger.info("sourceRefNum is null for given refNum {} ", refNum);
            result.put("companyName", "default");
        }
        return result;
    }

    public List<Document> getSystemIcons(String refNum, Optional<String> templateTheme) {
        List<Document> resp = null;
        try {
            Document query = new Document(REFNUM, refNum);
            query.append("type", "system");
            if (templateTheme.isPresent()) {
                query.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                List<Document> orQueries = new ArrayList<>();
            //                orQueries.add(new Document(TEMPLATE_THEME, "default"));
            //                orQueries.add(new Document(TEMPLATE_THEME, new Document("$exists", false)));
            //                query.put("$or", orQueries);
            //            }
            resp = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, query);
            List<String> exisitingSystemIcons = new ArrayList<>();
            for (Document document : resp) {
                if (document.getString("name") != null) {
                    exisitingSystemIcons.add(document.getString("name"));
                }
            }
            query.put(REFNUM, CANVUS_REFNUM);
            query.put("name", new Document("$nin", exisitingSystemIcons));
            List<Document> defaultResp = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, query);
            for (Document document : defaultResp) {
                resp.add(document);
            }
        } catch (Exception e) {
            logger.info("exception while getSystemIcons {}", e);
        }
        return resp;
    }

    public List<Document> getCustomIcons(String refNum, Optional<String> templateTheme, String theme) {
        List<Document> resp = null;
        try {
            Document query = new Document(REFNUM, refNum);
            query.append("type", "custom");
            query.append(THEME, theme);
            if (templateTheme.isPresent()) {
                query.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                query.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            resp = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, query);
        } catch (Exception e) {
            logger.info("exception while getCustomIcons {}", e);
        }
        return resp;
    }

    public boolean saveCustomIcon(String refNum, String svgContent, String iconName, Optional<String> templateTheme, String theme, Response response) {
        try {
            //            svgContent = svgContent.replaceAll("svg", "symbol");
            svgContent = normalizeSvgContent(svgContent);
            Document insertDoc = new Document();
            insertDoc.put(REFNUM, refNum);
            insertDoc.put("type", "custom");
            insertDoc.put("name", iconName.trim());
            insertDoc.put(THEME, theme);
            if (mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, insertDoc)) {
                response.setErrorMsg("icon already exists with same name");
                return false;
            }
            String normalizedIconId = siteUtil.normalize(iconName);
            normalizedIconId = "cms-canvas-custom-icons-" + normalizedIconId;
            if (svgContent.contains("<?xml version=\"1.0\" ?>")) {
                svgContent = svgContent.replace("<?xml version=\"1.0\" ?>", "");
            }
            org.jsoup.nodes.Document svgDoc = HtmlParser.parse(svgContent);
            for (Attribute symbol : svgDoc.getElementsByTag("svg").get(0).attributes().asList()) {
                if (!symbol.getKey().equalsIgnoreCase("viewbox")) {
                    svgDoc.getElementsByTag("svg").get(0).removeAttr(symbol.getKey());
                }
            }
            if (svgDoc.getElementsByTag("svg").size() > 0) {
                svgDoc.getElementsByTag("svg").get(0).attr("id", normalizedIconId);
            } else {
                logger.info("not found symbol attr hence not updating id for svg element {} {}", refNum, svgDoc);
            }
            insertDoc.put("svgContent", svgDoc.toString());
            insertDoc.put("iconId", normalizedIconId);
            if (templateTheme.isPresent()) {
                insertDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            mongoManager.insertDocument(insertDoc, Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db);
            Document queryDoc = new Document();
            queryDoc.append(REFNUM, refNum);
            queryDoc.append(THEME, theme);
            //            queryDoc.append("type", "custom");
            if (templateTheme.isPresent()) {
                queryDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                queryDoc.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            List<Document> iconsList = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, queryDoc);
            String allIconsData = "  <svg xmlns=\"http://www.w3.org/2000/svg\">";
            for (Document document : iconsList) {
                if (document.getString("svgContent") != null) {
                    String svgData = document.getString("svgContent").replaceAll("<svg", "<symbol");
                    svgData = svgData.replaceAll("</svg", "</symbol");
                    allIconsData += "\n" + svgData;
                }
            }
            allIconsData += "\n" + "  </svg>\n";
            Document upsertQueryDoc = new Document();
            upsertQueryDoc.append(REFNUM, refNum);
            upsertQueryDoc.append(THEME, theme);
            if (templateTheme.isPresent()) {
                upsertQueryDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                upsertQueryDoc.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            String s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/";
            if (templateTheme.isPresent()) {
                s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/" + templateTheme.get() + "/";
            }
            String oldFile = "";
            //            if (templateTheme.isPresent()) {
            //                oldFile = deleteOldFile(CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, upsertQueryDoc, "url",
            //                        s3PathForGlobalBrandingCssFile, "custom");
            //
            //            } else {
            //                oldFile = deleteOldFile(CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, upsertQueryDoc, "url",
            //                        s3PathForGlobalBrandingCssFile, "custom-icons");
            //            }
            String filePath = uploadIcons(allIconsData, refNum, templateTheme, Optional.of(theme), "custom");
            logger.info("uploaded svg icon path is {}", filePath);
            Document newDoc = new Document();
            newDoc.append("url", filePath);
            newDoc.append(REFNUM, refNum);
            newDoc.append(THEME, theme);
            if (templateTheme.isPresent()) {
                newDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            mongoManager.upsert(upsertQueryDoc, new Document("$set", newDoc), CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, db);
            /*if (!DEFAULT.equalsIgnoreCase(theme)) {
            }*/
            Document themeQuery = new Document(REFNUM, refNum).append(THEME_ID, theme);
            Document updateDoc = new Document(UPDATED_DATE, new Date());
            Document themeDoc = mongoManager.findDocument(CANVAS_SITE_THEMES_METADATA, db, themeQuery);
            if (themeDoc != null) {
                String status = themeDoc.getString(STATUS_KEY);
                if ("published".equalsIgnoreCase(status)) {
                    updateDoc.put(STATUS_KEY, "unpublished");
                }
            }
            mongoManager.upsert(themeQuery, new Document("$set", updateDoc), CANVAS_SITE_THEMES_METADATA, db);
            return true;
        } catch (Exception e) {
            response.setErrorMsg(e.toString());
            logger.info("exception while saveCustomIcon {}", e);
        }
        return false;
    }

    public boolean saveSystemIcon(String svgContent, String iconName) {
        try {
            //            svgContent = svgContent.replaceAll("svg", "symbol");
            svgContent = normalizeSvgContent(svgContent);
            if (svgContent.contains("<?xml version=\"1.0\" ?>")) {
                svgContent = svgContent.replace("<?xml version=\"1.0\" ?>", "");
            }
            Document upSertDoc = new Document();
            upSertDoc.put(REFNUM, CANVUS_REFNUM);
            upSertDoc.put("type", "system");
            String normalizedIconId = siteUtil.normalize(iconName);
            //            normalizedIconId = "cms-canvas-system-icons-" + normalizedIconId;
            org.jsoup.nodes.Document svgDoc = HtmlParser.parse(svgContent);
            Document newDoc = new Document(upSertDoc);
            newDoc.put("name", iconName);
            newDoc.put("svgContent", svgDoc.toString());
            if (svgDoc.getElementsByTag("svg").get(0).attr("id") != null) {
                String iconId = svgDoc.getElementsByTag("svg").get(0).attr("id");
                iconId = iconId.replaceAll("[\\n\\r]", "");
                newDoc.put("iconId", iconId);
            } else {
                logger.info("icon id is not present for {} henece keeping name as id ", iconName);
                newDoc.put("iconId", iconName);
            }
            upSertDoc.put("iconId", newDoc.getString("iconId"));
            //            for (Attribute symbol : svgDoc.getElementsByTag("svg").get(0).attributes().asList()) {
            //                if (symbol.getKey().equalsIgnoreCase("id")) {
            //                    svgDoc.getElementsByTag("svg").get(0).attr(symbol.getKey());
            //                }
            //            }
            //            if (svgDoc.getElementsByTag("svg").size() > 0) {
            //                svgDoc.getElementsByTag("svg").get(0).attr("id", normalizedIconId);
            //            } else {
            //                logger.info("not found symbol attr hence not updating id for svg element {} {}", "CANVAS", svgDoc);
            //            }
            mongoManager.upsert(upSertDoc, new Document("$set", newDoc), Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db);
            Document queryDoc = new Document();
            queryDoc.append(REFNUM, CANVUS_REFNUM);
            queryDoc.append("type", "system");
            List<Document> iconsList = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, queryDoc);
            String allIconsData = "  <svg xmlns=\"http://www.w3.org/2000/svg\">";
            for (Document document : iconsList) {
                if (document.getString("svgContent") != null) {
                    String svgData = document.getString("svgContent").replaceAll("<svg", "<symbol");
                    svgData = svgData.replaceAll("</svg", "</symbol");
                    allIconsData += "\n" + svgData;
                }
            }
            allIconsData += "\n" + "  </svg>\n";
            Document upsertQueryDoc = new Document();
            upsertQueryDoc.append(REFNUM, CANVUS_REFNUM);
            upsertQueryDoc.put("type", "system");
            String s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + CANVUS_REFNUM + "/canvas/";
            //            String oldFile = deleteOldFile(CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, upsertQueryDoc, "url",
            //                    s3PathForGlobalBrandingCssFile, "system-icons");
            String filePath = uploadIcons(allIconsData, CANVUS_REFNUM, Optional.empty(), Optional.empty(), "system");
            logger.info("uploaded svg icon path is {}", filePath);
            Document newDoc1 = new Document();
            newDoc1.append("url", filePath);
            newDoc1.append(REFNUM, CANVUS_REFNUM);
            mongoManager.upsert(upsertQueryDoc, new Document("$set", newDoc1), CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, db);
            return true;
        } catch (Exception e) {
            logger.info("exception while saveCustomIcon {}", e);
        }
        return false;
    }

    public String uploadIcons(String allIconsData, String refNum, Optional<String> templateTheme, Optional<String> theme, String type) {
        String s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/icons/" + type + "-icons-" + System.currentTimeMillis() + ".svg";
        if (theme.isPresent()) {
            if (!DEFAULT.equalsIgnoreCase(theme.get())) {
                s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/icons/" + theme.get() + "/" + type + "-icons-" + System.currentTimeMillis() + ".svg";
            }
        }
        if (templateTheme.isPresent()) {
            s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/icons/" + templateTheme.get() + "/" + type + "-" + System.currentTimeMillis() + ".svg";
        }
        siteUtil.uploadFileToS3WithType(allIconsData, s3PathForGlobalBrandingCssFile, "image/svg+xml");
        String cdnRootPath = conf.getString("cdn-url").replace("/" + conf.getString("s3.root"), "/");
        return cdnRootPath + s3PathForGlobalBrandingCssFile;
    }

    public String normalizeSvgContent(String svgContent) throws Exception {
        if (!svgContent.contains("<svg") || !svgContent.contains("</svg>")) {
            throw new Exception("not a valid svg file");
        }
        svgContent = svgContent.substring(svgContent.indexOf("<svg"));
        svgContent = svgContent.substring(0, svgContent.indexOf("</svg>"));
        svgContent += "</svg>";
        logger.info("normalized svg content is {}", svgContent);
        return svgContent;
    }

    public Document replaceSystemIcon(String refNum, String iconName, String svgContent, String type, Optional<String> templateTheme, String theme, String iconId) {
        try {
            normalizeSvgContent(svgContent);
            Document activityResp = new Document();
            Document insertDoc = new Document();
            insertDoc.append(REFNUM, refNum);
            insertDoc.append("type", type);
            if ("custom".equalsIgnoreCase(type)) {
                insertDoc.append(THEME, theme);
            }
            insertDoc.append("name", iconName.trim());
            Document queryDoc = new Document(insertDoc);
            activityResp.put("previousValue", insertDoc);
            String normalizedIconId = iconId;
            org.jsoup.nodes.Document svgDoc = HtmlParser.parse(svgContent);
            for (Attribute symbol : svgDoc.getElementsByTag("svg").get(0).attributes().asList()) {
                if (!symbol.getKey().equalsIgnoreCase("viewbox")) {
                    svgDoc.getElementsByTag("svg").get(0).removeAttr(symbol.getKey());
                }
            }
            if (svgDoc.getElementsByTag("svg").size() > 0) {
                svgDoc.getElementsByTag("svg").get(0).attr("id", normalizedIconId);
            } else {
                logger.info("not found svg attr hence not updating id for svg element {} {}", refNum, svgDoc);
            }
            insertDoc.append("svgContent", svgDoc.toString());
            insertDoc.put("iconId", iconId);
            activityResp.put("newValue", insertDoc);
            if (templateTheme.isPresent()) {
                queryDoc.put(TEMPLATE_THEME, templateTheme.get());
                insertDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                queryDoc.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            mongoManager.upsert(queryDoc, new Document("$set", insertDoc), Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db);
            generateGlobalBrandingIconsFile(refNum, templateTheme, theme);
            CompletableFuture.runAsync(() -> {
                Map<String, Object> params = new HashMap<>();
                params.put(REFNUM, refNum);
                params.put("iconName", iconName);
                params.put("type", type);
                params.put("svgContent", svgContent);
                if (templateTheme.isPresent()) {
                    params.put(TEMPLATE_THEME, templateTheme);
                }
                String url = conf.getString("preprod.canvas.replaceSystemIcon");
                //                iconsPreProdUpdate(Json.toJson(params), url);
            });
            return activityResp;
        } catch (Exception e) {
            logger.info("exception while replaceSystemIcon {}", e);
        }
        return null;
    }

    public Document renameCustomIcon(JsonNode json) {
        try {
            Optional<String> templateTheme = Optional.empty();
            if (json.hasNonNull(TEMPLATE_THEME)) {
                templateTheme = Optional.of(json.get(TEMPLATE_THEME).asText());
            }
            String theme = DEFAULT;
            if (json.hasNonNull(THEME) && !json.get(THEME).asText().isEmpty()) {
                theme = json.get(THEME).asText();
            }
            Document resp = new Document();
            Document queryDoc = new Document();
            queryDoc.put(REFNUM, json.get(REFNUM).asText());
            queryDoc.put("type", "custom");
            queryDoc.put("name", json.get("name").asText().trim());
            if (templateTheme.isPresent()) {
                queryDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                queryDoc.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            queryDoc.put(THEME, theme);
            Document newDoc = new Document();
            newDoc.put("name", json.get("newName").asText());
            logger.info("{},{}", queryDoc, newDoc);
            resp.put("previousValue", queryDoc.getString("name"));
            resp.put("newValue", newDoc);
            mongoManager.upsert(queryDoc, new Document("$set", newDoc), Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db);
            /*if (!DEFAULT.equalsIgnoreCase(theme)) {

            }*/
            Document themeQuery = new Document(REFNUM, json.get(REFNUM).asText()).append(THEME_ID, theme);
            Document updateDoc = new Document(UPDATED_DATE, new Date());
            Document themeDoc = mongoManager.findDocument(CANVAS_SITE_THEMES_METADATA, db, themeQuery);
            if (themeDoc != null) {
                String status = themeDoc.getString(STATUS_KEY);
                if ("published".equalsIgnoreCase(status)) {
                    updateDoc.put(STATUS_KEY, "unpublished");
                }
            }
            mongoManager.upsert(themeQuery, new Document("$set", updateDoc), CANVAS_SITE_THEMES_METADATA, db);
            CompletableFuture.runAsync(() -> {
                String url = conf.getString("preprod.canvas.renameCustomIcon");
                iconsPreProdUpdate(json, url);
            });
            return resp;
        } catch (Exception e) {
            logger.info("exception while renameCustomIcon {}", e);
        }
        return null;
    }

    private void iconsPreProdUpdate(JsonNode json, String url) {
        ObjectMapper mapper = new ObjectMapper();
        Map<String, Object> params = mapper.convertValue(json, new TypeReference<Map<String, Object>>() {
        });
        logger.info("making service call for renameCustomIconInPreProd with {} and {}", url, json);
        remoteServiceCaller.sendPostASync(url, params);
    }

    public String downloadIcon(JsonNode json) {
        try {
            String svgCode = json.get("svgCode").asText();
            String refNum = json.get(REFNUM).asText();
            Optional<String> templateTheme = Optional.empty();
            if (json.hasNonNull(TEMPLATE_THEME)) {
                templateTheme = Optional.of(json.get(TEMPLATE_THEME).asText());
            }
            String theme = DEFAULT;
            if (json.hasNonNull(THEME) && !json.get(THEME).asText().isEmpty()) {
                theme = json.get(THEME).asText();
            }
            org.jsoup.nodes.Document svgDoc = HtmlParser.parse(svgCode);
            svgDoc.getElementsByTag("svg").get(0).attr("xmlns", "http://www.w3.org/2000/svg");
            svgCode = svgDoc.toString();
            return uploadIcons(svgCode, refNum, templateTheme, Optional.of(theme), "custom");
        } catch (Exception e) {
            logger.error("exception while generation download link for icon {}", e);
        }
        return null;
    }

    public Boolean isCaasMappingPresent(String widgetId) {
        return mongoManager.checkIfDocumentExists(CANVAS_CAAS_SITE_CONTENT_MAPPING, db, new Document(WIDGET_ID_FIELD, widgetId));
    }

    public Document getWidgetFromWidgetId(String widgetId) {
        org.bson.Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
        if (widgetDoc == null) {
            return null;
        }
        return getCanvasViewDocument(widgetDoc.getString(VIEW_ID));
    }

    public List<String> getCardContentTypes(String widgetId) {
        try {
            List<String> respDocs = mongoManager.getUniqueList("contentType", new Document(WIDGET_ID_FIELD, widgetId), CANVAS_CAAS_SITE_CONTENT_MAPPING, db);
            return respDocs;
        } catch (Exception e) {
            logger.error("exception with getCardContentTypes {}", e);
            return null;
        }
    }

    public void generateGlobalBrandingIconsFile(String refNum, Optional<String> templateTheme, String theme) {
        try {
            Document queryDoc = new Document();
            queryDoc.append(REFNUM, refNum);
            queryDoc.append("theme", theme);
            // we need all types of icons. both system and custom.
            //            queryDoc.append("type", "custom");
            if (templateTheme.isPresent()) {
                queryDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            // removing because we need only one icon file for a site
            //            else {
            //                queryDoc.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            List<Document> iconsList = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, queryDoc);
            String allIconsData = "  <svg xmlns=\"http://www.w3.org/2000/svg\">";
            for (Document document : iconsList) {
                if (document.getString("svgContent") != null) {
                    String svgData = document.getString("svgContent").replaceAll("<svg", "<symbol");
                    svgData = svgData.replaceAll("</svg", "</symbol");
                    allIconsData += "\n" + svgData;
                }
            }
            allIconsData += "\n" + "  </svg>\n";
            Document upsertQueryDoc = new Document();
            upsertQueryDoc.append(REFNUM, refNum);
            upsertQueryDoc.append("theme", theme);
            String s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/";
            if (templateTheme.isPresent()) {
                s3PathForGlobalBrandingCssFile = conf.getString("s3.root") + "/" + refNum + "/canvas/" + templateTheme.get() + "/";
            }
            //            String oldFile = "";
            //            if (templateTheme.isPresent()) {
            //                oldFile = deleteOldFile(CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, upsertQueryDoc, "url",
            //                        s3PathForGlobalBrandingCssFile, "custom");
            //            } else {
            //                oldFile = deleteOldFile(CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, upsertQueryDoc, "url",
            //                        s3PathForGlobalBrandingCssFile, "custom-icons");
            //
            //            }
            String filePath = uploadIcons(allIconsData, refNum, templateTheme, Optional.empty(), "custom");
            logger.info("uploaded svg icon path is {}", filePath);
            if (templateTheme.isPresent()) {
                upsertQueryDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            //            else {
            //                upsertQueryDoc.put(TEMPLATE_THEME, new Document("$exists", false));
            //            }
            Document newDoc = new Document();
            newDoc.append("url", filePath);
            newDoc.append(REFNUM, refNum);
            if (templateTheme.isPresent()) {
                newDoc.put(TEMPLATE_THEME, templateTheme.get());
            }
            mongoManager.upsert(upsertQueryDoc, new Document("$set", newDoc), CANVAS_SITE_GLOBAL_BRANDING_ICONS_URLS, db);
        } catch (Exception e) {
            logger.error("Exception in generateGlobalBrandingIconsFile : ", e);
        }
    }

    public boolean deleteCustomIcon(JsonNode payload) {
        try {
            String refNum = payload.get(REFNUM).asText();
            String iconName = payload.get("name").asText();
            String theme = DEFAULT;
            if (payload.hasNonNull(THEME) && !payload.get(THEME).asText().isEmpty()) {
                theme = payload.get(THEME).asText();
            }
            Document deleteDoc = new Document(REFNUM, refNum);
            deleteDoc.append("name", iconName);
            deleteDoc.append(THEME, theme);
            mongoManager.deleteDocument(Constants.CANVAS_SITE_GLOBAL_BRANDING_ICONS, db, deleteDoc);
            /*if (!DEFAULT.equalsIgnoreCase(theme)) {

            }*/
            Document themeQuery = new Document(REFNUM, refNum).append(THEME_ID, theme);
            Document updateDoc = new Document(UPDATED_DATE, new Date());
            Document themeDoc = mongoManager.findDocument(CANVAS_SITE_THEMES_METADATA, db, themeQuery);
            if (themeDoc != null) {
                String status = themeDoc.getString(STATUS_KEY);
                if ("published".equalsIgnoreCase(status)) {
                    updateDoc.put(STATUS_KEY, "unpublished");
                }
            }
            mongoManager.upsert(themeQuery, new Document("$set", updateDoc), CANVAS_SITE_THEMES_METADATA, db);
            CompletableFuture.runAsync(() -> {
                String url = conf.getString("preprod.canvas.deleteCustomIcon");
                iconsPreProdUpdate(payload, url);
            });
            return true;
        } catch (Exception e) {
            logger.error("error while deleting icons {}", e);
            return false;
        }
    }

    public Document getCaasViewAndMapping(String refNum, String widgetId, String contentType) {
        try {
            Document queryDoc = new Document();
            Document resp = new Document();
            queryDoc.append(REFNUM, refNum);
            queryDoc.append(WIDGET_ID_FIELD, widgetId);
            Document widgetDoc = mongoManager.findDocument(Constants.CANVAS_SITEWIDGETS, db, queryDoc);
            Document viewDoc = new Document();
            queryDoc.remove(WIDGET_ID_FIELD);
            if (widgetDoc != null && widgetDoc.getString(VIEW_ID) != null) {
                queryDoc.append(VIEW_ID, widgetDoc.getString(VIEW_ID));
                viewDoc = mongoManager.findDocument(Constants.CANVAS_SITEWIDGETVIEWS, db, queryDoc);
            } else {
                widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, new Document(WIDGET_ID_FIELD, widgetId).append(LATEST, true));
                if (widgetDoc == null || widgetDoc.getString(VIEW_ID) != null) {
                    logger.info("widget or viewId{} is not found ", widgetId);
                }
                queryDoc.remove(REFNUM);
                queryDoc.append(VIEW_ID, widgetDoc.getString(VIEW_ID));
                queryDoc.append(LATEST, true);
                viewDoc = preProdMongoManager.findDocument(Constants.CANVAS_GLOBALWIDGETVIEWS, preprodDb, queryDoc);
            }
            if (viewDoc != null && viewDoc.getString("viewHtml") != null) {
                resp.append("viewHtml", viewDoc.getString("viewHtml"));
            } else {
                logger.info("view html not found refNum: {}, widgetId: {}");
            }
            List<Document> contentDocs = mongoManager.findAllDocuments(Constants.CANVAS_CAAS_CONTENT_GLOBAL_MAPPING, db, new Document("contentType", contentType));
            resp.append("mapping", contentDocs);
            return resp;
        } catch (Exception e) {
            logger.error("exception in canvasWidgetContentMapping {}", e);
            return null;
        }
    }

    public String getDeviceSpecificContentKey(String device) {
        return device + "_" + TAG_CONTENT;
    }

    public Map<String, List<Document>> getIcons(String refNum, Optional<String> templateTheme, String theme) {
        Map<String, List<Document>> resp = new HashMap<>();
        resp.put("systemIcons", getSystemIcons(refNum, templateTheme));
        resp.put("customIcons", getCustomIcons(refNum, templateTheme, theme));
        return resp;
    }

    public void removeAttFromAgWidget(boolean status, Element ele) {
        if (!status && ele.hasAttr(MIGRATED_WIDGET_IDENTIFIER)) {
            logger.debug("Removing hide-on-mobile attribute from migrated widgets");
            Elements asElements = ele.getElementsByAttribute(AS_ELEMENT);
            if (!asElements.isEmpty()) {
                Element asElement = asElements.first();
                asElement.removeAttr(HIDE_ON_MOBILE);
            }
        }
    }

    public void updateWidgetVisibilityStatus(String refNum, String locale, String pageId, String instanceId, boolean status) {
        String pageKey = "ph:page:" + refNum + ":" + MOBILE + ":" + locale + ":" + pageId;
        String pageVal = redisManager.get(pageKey);
        if (pageVal != null) {
            Page p = Json.fromJson(Json.parse(pageVal), Page.class);
            String html = p.getPageHtml();
            if (html != null) {
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(html);
                Elements instaceElements = pageDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId);
                if (instaceElements != null && !instaceElements.isEmpty()) {
                    Element ele = instaceElements.first();
                    removeAttFromAgWidget(status, ele);
                    if (status) {
                        ele.addClass("phw-d-none");
                    } else {
                        ele.removeClass("phw-d-none");
                    }
                    ele.attr("has-edit", "true");
                    p.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(p).toString());
                }
            }
        }
        pageKey = "ph:page:" + refNum + ":" + DESKTOP + ":" + locale + ":" + pageId;
        pageVal = redisManager.get(pageKey);
        if (pageVal != null) {
            Page p = Json.fromJson(Json.parse(pageVal), Page.class);
            String html = p.getPageHtml();
            if (html != null) {
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(html);
                Elements instaceElements = pageDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId);
                if (instaceElements != null && !instaceElements.isEmpty()) {
                    Element ele = instaceElements.first();
                    removeAttFromAgWidget(status, ele);
                    if (status) {
                        ele.attr("hide-on-mobile", "true");
                    } else {
                        ele.removeAttr("hide-on-mobile");
                    }
                    ele.attr("has-edit", "true");
                    p.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(p).toString());
                }
            }
        }
    }

    public void placeRestoredWidgetInPage(CanvasDragDropRequest canvasDragDropRequest, Element desktopPageEle, Element mobilePageEle, Boolean migratedSite) {
        List<String> devices;
        String targetDevice = canvasDragDropRequest.getTargetDevice();
        if (targetDevice != null && !targetDevice.isEmpty() && !DESKTOP.equalsIgnoreCase(targetDevice)) {
            devices = Arrays.asList(canvasDragDropRequest.getTargetDevice());
        } else {
            devices = Arrays.asList(DESKTOP, MOBILE);
        }
        for (String device : devices) {
            String pageKey = SiteUtil.constructPhPageKey(canvasDragDropRequest.getRefNum(), device, canvasDragDropRequest.getLocale(), canvasDragDropRequest.getPageId());
            String pageContent = redisManager.get(pageKey);
            Page page = Json.fromJson(Json.parse(pageContent), Page.class);
            org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
            String structure = null;
            if (!migratedSite || (device.equalsIgnoreCase(DESKTOP) && desktopPageEle != null)) {
                structure = desktopPageEle.toString();
            } else if (device.equalsIgnoreCase(MOBILE) && mobilePageEle != null) {
                structure = mobilePageEle.toString();
            }
            boolean placeWidget = false;
            if (canvasDragDropRequest.getNextSiblingId() != null) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getNextSiblingId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.before(structure);
                    placeWidget = true;
                }
            }
            if (canvasDragDropRequest.getPreviousSiblingId() != null && !placeWidget) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getPreviousSiblingId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.after(structure);
                    placeWidget = true;
                }
            }
            if (canvasDragDropRequest.getParentElementId() != null && !placeWidget) {
                Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, canvasDragDropRequest.getParentElementId());
                if (!elements.isEmpty()) {
                    Element element = elements.get(0);
                    element.prepend(structure);
                    placeWidget = true;
                }
            }
            if (!placeWidget) {
                logger.error("Unknown place to add new value as NextSiblingId, PreviousSiblingId and ParentElementId are nulls for canavas widget addition");
            }
            page.setPageHtml(pageDoc.toString());
            redisManager.set(pageKey, Json.toJson(page).toString());
        }
    }

    public String getDeviceSpecificContent(Document contentDoc, String device) {
        String content;
        if (!DESKTOP.equalsIgnoreCase(device)) {
            String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
            if (contentDoc.containsKey(deviceSpecificContentKey) && contentDoc.get(deviceSpecificContentKey) != null) {
                content = contentDoc.getString(deviceSpecificContentKey);
            } else {
                content = contentDoc.getString(TAG_CONTENT);
            }
        } else {
            content = contentDoc.getString(TAG_CONTENT);
        }
        return content;
    }

    public void handleDeviceSpecificContent(Map<String, List<Map<String, Object>>> contentMap, String device) {
        String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
        for (String key : contentMap.keySet()) {
            if (!key.equalsIgnoreCase("cards")) {
                List<Map<String, Object>> tagContent = contentMap.get(key);
                for (Map<String, Object> attrData : tagContent) {
                    if (!DESKTOP.equalsIgnoreCase(device)) {
                        String desktopContent = (String) attrData.get(TAG_CONTENT);
                        attrData.put(DESKTOP_CONTENT, desktopContent);
                        if (attrData.containsKey(deviceSpecificContentKey) && attrData.get(deviceSpecificContentKey) != null) {
                            String content = (String) attrData.get(deviceSpecificContentKey);
                            attrData.put(TAG_CONTENT, content);
                        }
                    }
                }
            } else {
                List<Map<String, Object>> cardsData = contentMap.get(key);
                for (Map<String, Object> card : cardsData) {
                    for (String cardPs : card.keySet()) {
                        List<Map<String, Object>> tagData = (List<Map<String, Object>>) card.get(cardPs);
                        for (Map<String, Object> attrData : tagData) {
                            if (!DESKTOP.equalsIgnoreCase(device)) {
                                String desktopContent = (String) attrData.get(TAG_CONTENT);
                                attrData.put(DESKTOP_CONTENT, desktopContent);
                                if (attrData.containsKey(deviceSpecificContentKey) && attrData.get(deviceSpecificContentKey) != null) {
                                    String content = (String) attrData.get(deviceSpecificContentKey);
                                    attrData.put(TAG_CONTENT, content);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    public boolean syncWithDesktop(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device, boolean globalWidget) {
        try {
            String deviceSpecificContentKey = getDeviceSpecificContentKey(device);
            Document query = new Document(REFNUM, refNum).append(LOCALE, locale).append(INSTANCE_ID_FIELD, instanceId).append(deviceSpecificContentKey, new Document("$exists", true));
            if (!globalWidget) {
                query.append(PERSONA, siteVariant).append(PAGE_ID, pageId);
            }
            List<Document> documents = mongoManager.findAllDocuments(CANVAS_SITE_CONTENT, db, query);
            for (Document doc : documents) {
                Document updateDoc = new Document(doc);
                updateDoc.remove(deviceSpecificContentKey);
                mongoManager.deleteDocument(CANVAS_SITE_CONTENT, db, doc);
                mongoManager.insertDocument(updateDoc, CANVAS_SITE_CONTENT, db);
            }
            syncDesignSettingsWithDesktop(refNum, locale, siteVariant, pageId, instanceId, device);
            addOrRemoveDeviceOverriddenAttr(refNum, locale, pageId, instanceId, device, "remove");
            makeSyncedWidgetDirty(refNum, locale, siteVariant, pageId, instanceId, device);
            return true;
        } catch (Exception e) {
            logger.info("Failed to sync to desktop for widget with instance-id {}", instanceId);
            return false;
        }
    }

    public void makeSyncedWidgetDirty(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device) {
        String pageKey = RedisKeyUtil.getPageKey(refNum, device, locale, pageId);
        String pageValue = redisManager.get(pageKey);
        if (pageValue != null) {
            Page p = Json.fromJson(Json.parse(pageValue), Page.class);
            String pageHtml = p.getPageHtml();
            org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageHtml);
            Elements sectionEles = pageDoc.getElementsByTag(SECTION);
            if (sectionEles != null) {
                for (Element sectionEle : sectionEles) {
                    if (sectionEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR) && sectionEle.hasAttr(INSTANCE_ID_FIELD) && sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                        sectionEle.attr(CANVAS_EDIT_ATTR, "true");
                    }
                }
            }
            p.setPageHtml(pageDoc.toString());
            redisManager.set(pageKey, Json.toJson(p).toString());
        }
    }

    public String deleteOldFile(String collection, Document query, String key, String filePath, String fileName) {
        try {
            logger.debug("In method deleteOldFile, collection : {}, filepath : {}", collection, filePath);
            Document doc = mongoManager.findDocument(collection, conf.getString(MONGO_DB), query);
            String oldFile = "";
            if (doc != null) {
                oldFile = doc.getString(key);
                logger.debug("backup file : {}", oldFile);
                s3Manager.deleteObjectsContaining(filePath, fileName, oldFile.split(fileName)[1]);
            }
            return oldFile;
        } catch (Exception e) {
            logger.error("exception while deleteOldFile -->", e);
            return "";
        }
    }

    public boolean canHaveCaasContent(Document contentDoc) {
        String node = contentDoc.getString(NODE);
        String contentKey = contentDoc.getString(CONTENT_KEY);
        return (node.equalsIgnoreCase("a") && contentKey.equalsIgnoreCase(HREF)) || (node.equalsIgnoreCase("img") && contentKey.equalsIgnoreCase("src")) || (node.equalsIgnoreCase(VIDEO_TAG_IDENTIFIER) && contentKey.equalsIgnoreCase(VIDEO_TAG_IDENTIFIER));
    }

    public void uploadRootFile(String refNum, Optional<String> templateTheme, String theme) {
        List<String> projections = new ArrayList<>();
        projections.add("name");
        projections.add(VALUE);
        String dbName = conf.getString(MONGO_DB);
        Document query = new Document(REFNUM, refNum).append(THEME, theme);
        if (templateTheme.isPresent()) {
            query.put(TEMPLATE_THEME, templateTheme.get());
        }
        List<Document> docs = mongoManager.findAllDocuments("canvas_site_global_branding_variables", dbName, query);
        Map<String, String> variablesMap = new HashMap<>();
        docs.forEach(doc -> {
            variablesMap.put(doc.getString("name"), doc.getString(VALUE));
        });
        String rootFile = siteUtil.generateRootFile(variablesMap);
        logger.info("getnerated root file -> {}", rootFile);
        String s3PathForGlobalBrandingCssFile = conf.getString(S3_ROOT) + "/" + refNum + "/canvas/common" + "/canvas-global-branding" + System.currentTimeMillis() + ".css";
        if (templateTheme.isPresent()) {
            s3PathForGlobalBrandingCssFile = conf.getString(S3_ROOT) + "/" + refNum + "/canvas/" + templateTheme.get() + "/common" + "/canvas-global-branding" + System.currentTimeMillis() + ".css";
        }
        if (templateTheme.isPresent()) {
            query.put(TEMPLATE_THEME, templateTheme.get());
        } else {
            query.put(TEMPLATE_THEME, new Document("$exists", false));
        }
        query.put("type", "siteBrandingCss");
        //        String oldFile = canvasService.deleteOldFile(CANVAS_GLOBAL_DESIGN_FILES, query, "cdnUrl",
        //                s3PathForGlobalBrandingCssFile.split("canvas-global-branding")[0], "canvas-global-branding");
        if (!theme.equalsIgnoreCase(DEFAULT)) {
            s3PathForGlobalBrandingCssFile = conf.getString(S3_ROOT) + "/" + refNum + "/canvas/" + theme + "/common" + "/canvas-global-branding" + System.currentTimeMillis() + ".css";
        }
        siteUtil.uploadFileToS3(rootFile, s3PathForGlobalBrandingCssFile);
        String cdnRootPath = conf.getString("cdn-url").replace("/" + conf.getString(S3_ROOT), "/");
        Document updateDoc = new Document(REFNUM, refNum);
        updateDoc.put("theme", theme);
        updateDoc.put("type", "siteBrandingCss");
        updateDoc.put("cdnUrl", cdnRootPath + s3PathForGlobalBrandingCssFile);
        if (templateTheme.isPresent()) {
            updateDoc.put(TEMPLATE_THEME, templateTheme.get());
        }
        mongoManager.upsert(query, new Document("$set", updateDoc), CANVAS_GLOBAL_DESIGN_FILES, dbName);
    }

    public Optional<String> getThemeIdFromTheme(String refNum, String themeName) {
        Optional<String> themeId = Optional.empty();
        try {
            org.bson.Document query = new Document(REFNUM, refNum);
            query.append(THEMENAME, themeName);
            org.bson.Document doc = mongoManager.findDocument(Constants.CANVAS_SITE_THEMES_METADATA, conf.getString(MONGO_DB), query);
            logger.debug("result doc {}", doc);
            if (doc != null) {
                themeId = Optional.of(doc.getString(THEME_ID));
            }
        } catch (Exception e) {
            logger.error("EXception in getThemeID from theme refNum {} and themeName {} ", refNum, themeName);
        }
        return themeId;
    }

    public void updateDefaultTagStyles(Map<String, Object> jsonData, String isAzureImport, String refNum, String theme, boolean retain) {
        mongoManager.insertDocument(new Document("jsondata", jsonData).append(REFNUM, refNum), Constants.CANVAS_MASTER_DATA_VERSIONS, db);
        for (String type : jsonData.keySet()) {
            Map<String, Object> masterData = ((Map<String, Object>) jsonData.get(type));
            switch(type) {
                case "masterStyles":
                    {
                        Document queryDoc = new Document(REFNUM, refNum);
                        if (!refNum.equalsIgnoreCase(CANVUS_REFNUM)) {
                            queryDoc.put(THEME, theme);
                        }
                        mongoManager.deleteDocuments(queryDoc, CANVAS_SITE_CUSTOM_STYLES, db);
                        addMasterStyles(masterData, masterData, refNum, theme);
                    }
                    break;
                case "color":
                    {
                        if (!retain) {
                            removeDefaultGlobalStyle(refNum, type, theme);
                        }
                        addDefaultColors(masterData, type, refNum, theme, retain);
                    }
                    break;
                case "shadow":
                    {
                        if (!retain) {
                            removeDefaultGlobalStyle(refNum, type, theme);
                        }
                        addDefaultBoxShadow(masterData, type, refNum, theme, retain);
                    }
                    break;
                case "sizes":
                    {
                        if (!retain) {
                            removeDefaultGlobalStyle(refNum, type, theme);
                        }
                        addDefaultSizes(masterData, type, refNum, theme, retain);
                    }
                    break;
                case "themeVariables":
                    {
                        addDefaultThemeVariables(masterData, refNum, theme);
                    }
                    break;
                case "fontsizes":
                    {
                        if (!retain) {
                            removeDefaultGlobalStyle(refNum, type, theme);
                        }
                        addDefaultFontSizes(masterData, type, refNum, theme, retain);
                    }
                    break;
                default:
                    {
                        logger.info("mismatched type --> {}", type);
                    }
                    markDefaultStyleIdsAsSaved(refNum);
            }
        }
        if (isAzureImport.equalsIgnoreCase("true")) {
            Map<String, Object> payload = new HashMap<>();
            payload.put("data", jsonData);
            String url = conf.getString("url.importDefaultTagStyles", "http://qa1-cms-siteedit.devaz.phenom.local/canvas/static/importDefaultTagStylesInAzure");
            siteUtil.callAzureEndPoint(url, Json.toJson(payload));
        }
        if (!refNum.equalsIgnoreCase(CANVUS_REFNUM)) {
            uploadRootFile(refNum, Optional.empty(), theme);
        }
    }

    public void addMasterStyles(Map<String, Object> masterDataType, Map<String, Object> jsonData, String refNum, String theme) {
        for (String tag : masterDataType.keySet()) {
            Map<String, Object> tagData = (Map<String, Object>) jsonData.get(tag);
            Map<String, Object> styleData = (Map<String, Object>) tagData.get("styles");
            String styleId = (String) tagData.get("styleId");
            String displayName = (String) tagData.get("displayName");
            String state = null;
            if (tagData.containsKey("state")) {
                state = (String) tagData.get("state");
            }
            String device = (String) tagData.get("device");
            String actualTag = (String) tagData.get("tag");
            updateStyle(CANVAS_REFNUM, actualTag, styleId, styleData, displayName, false, state, device, "system", new HashMap<>(), DEFAULT, new HashMap<>(), false, null, false, false, null, null, null, false, false);
        }
    }

    public void removeDefaultGlobalStyle(String refNum, String globalStyleGroup, String theme) {
        Document queryDoc = new Document(REFNUM, refNum);
        queryDoc.put(GROUP, globalStyleGroup);
        if (!refNum.equalsIgnoreCase(CANVUS_REFNUM)) {
            queryDoc.put(THEME, theme);
        }
        mongoManager.deleteDocuments(queryDoc, Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db);
    }

    public void addDefaultBoxShadow(Map<String, Object> masterData, String group, String refNum, String theme, boolean retain) {
        for (String shadowName : masterData.keySet()) {
            Map<String, String> fieldVsValue = (Map<String, String>) masterData.get(shadowName);
            Document doc = new Document(REFNUM, refNum);
            doc.put(GROUP, group);
            doc.put(THEME, theme);
            fieldVsValue.forEach((field, value) -> {
                doc.put(field, value);
            });
            if (retain) {
                Document existsQuery = new Document(REFNUM, refNum);
                existsQuery.put(NAME, doc.get(NAME).toString());
                existsQuery.put(THEME, theme);
                existsQuery.put(GROUP, group);
                if (mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, existsQuery)) {
                    continue;
                }
            }
            mongoManager.insertDocument(doc, Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db);
        }
    }

    public void addDefaultColors(Map<String, Object> masterData, String group, String refNum, String theme, boolean retain) {
        for (String colorGroup : masterData.keySet()) {
            List<Map<String, Object>> colorGroupList = (List<Map<String, Object>>) masterData.get(colorGroup);
            for (Map<String, Object> colorGroupData : colorGroupList) {
                Document doc = new Document(REFNUM, refNum);
                doc.put(COLOR_GROUP, colorGroup);
                doc.put(GROUP, group);
                doc.put(THEME, theme);
                doc.put(DISPLAY_NAME, colorGroupData.get(DISPLAY_NAME).toString());
                doc.put(NAME, colorGroupData.get(NAME).toString());
                doc.put(VALUE, colorGroupData.get(VALUE).toString());
                if (colorGroupData.containsKey("isBasic")) {
                    doc.put("isBasic", (boolean) colorGroupData.get("isBasic"));
                }
                if (retain) {
                    Document existsQuery = new Document(REFNUM, refNum);
                    existsQuery.put(NAME, colorGroupData.get(NAME).toString());
                    existsQuery.put(THEME, theme);
                    existsQuery.put(GROUP, group);
                    if (mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, existsQuery)) {
                        continue;
                    }
                }
                mongoManager.insertDocument(doc, Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db);
            }
        }
    }

    public void addDefaultSizes(Map<String, Object> masterData, String group, String refNum, String theme, boolean retain) {
        for (String sizes : masterData.keySet()) {
            Map<String, String> sizesData = (Map<String, String>) masterData.get(sizes);
            Document doc = new Document(REFNUM, refNum);
            doc.put(GROUP, group);
            doc.put(THEME, theme);
            doc.put(DISPLAY_NAME, sizesData.get(DISPLAY_NAME));
            doc.put(NAME, sizesData.get(NAME));
            doc.put(VALUE, sizesData.get(VALUE));
            if (retain) {
                Document existsQuery = new Document(REFNUM, refNum);
                existsQuery.put(NAME, sizesData.get(NAME));
                existsQuery.put(THEME, theme);
                existsQuery.put(GROUP, group);
                if (mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, existsQuery)) {
                    continue;
                }
            }
            mongoManager.insertDocument(doc, Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db);
        }
    }

    public void addDefaultThemeVariables(Map<String, Object> masterData, String refNum, String theme) {
        Document doc = new Document(REFNUM, refNum);
        doc.put("themeVariables", masterData.get("themeVariables"));
        mongoManager.deleteDocument(Constants.CANVAS_MASTER_THEME_VARIABLES, db, new Document(REFNUM, refNum));
        mongoManager.insertDocument(doc, Constants.CANVAS_MASTER_THEME_VARIABLES, db);
    }

    public boolean deleteStyle(String refNum, String tag, String styleId, String theme) {
        Map<String, Object> attrUsage = getStyleAttributeUsage(refNum, styleId, "style", theme);
        if ((int) attrUsage.get("count") > 0) {
            logger.info("Attr is already in use --> {}", attrUsage);
            return true;
        }
        Document queryDoc = new Document(REFNUM, refNum);
        queryDoc.put(TAG, tag);
        queryDoc.put(STYLE_ID, styleId);
        queryDoc.put(THEME, theme);
        List<Document> deletedStyleDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, queryDoc);
        if (deletedStyleDocs != null) {
            for (Document deletedStyleDoc : deletedStyleDocs) {
                deletedStyleDoc.remove("_id");
                mongoManager.insertDocument(deletedStyleDoc, DELETED_STYLES, db);
            }
        }
        mongoManager.deleteDocuments(queryDoc, CANVAS_SITE_CUSTOM_STYLES, conf.getString(MONGO_DB));
        addCustomStylesCssToPage(refNum, theme);
        timer.schedule(new TimerTask() {

            @Override
            public void run() {
                logger.info("widget style/attribute usage data generation started at {}", new Date());
                List<String> devices = Arrays.asList("desktop", "mobile");
                devices.forEach(deviceType -> {
                    generateStyleAttributesMetadata(refNum, styleId, null, deviceType, new HashMap<>(), theme);
                });
            }
        }, 3000);
        return true;
    }

    //replace style method
    public boolean replaceStyleUsage(String refNum, String oldStyleId, String newStyleId, String theme) {
        try {
            logger.info("replacing style usage for refNum: {}, oldStyleId: {}, newStyleId: {}", refNum, oldStyleId, newStyleId);
            Document queryDoc = new Document(REFNUM, refNum).append(STYLE_IDS, oldStyleId);
            List<String> collections = Arrays.asList(CANVAS_SITE_INSTANCE_SETTINGS, CANVAS_SAVED_WIDGETS, CANVAS_DELETED_WIDGETS);
            for (String collection : collections) {
                List<Document> styleDocs = mongoManager.findAllDocuments(collection, db, queryDoc);
                if (collection.equalsIgnoreCase(CANVAS_SAVED_WIDGETS) || collection.equalsIgnoreCase(CANVAS_DELETED_WIDGETS)) {
                    for (Document styleDoc : styleDocs) {
                        if (styleDoc.containsKey("settings") && styleDoc.get("settings") != null) {
                            styleDoc.remove("_id");
                            Map<String, Object> settingsMap = (Map<String, Object>) styleDoc.get("settings");
                            replaceStyleId(settingsMap, oldStyleId, newStyleId);
                            Set<String> styleIdsSet = new HashSet<>();
                            widgetUtil.extractStyleIds(settingsMap, styleIdsSet);
                            Document updateDoc = new Document("settings", settingsMap).append(STYLE_IDS, styleIdsSet);
                            styleDoc.remove("settings");
                            styleDoc.remove(STYLE_IDS);
                            mongoManager.upsert(styleDoc, new Document("$set", updateDoc), collection, db);
                        }
                    }
                } else {
                    for (Document styleDoc : styleDocs) {
                        if (styleDoc.containsKey("settings") && styleDoc.get("settings") != null) {
                            styleDoc.remove("_id");
                            String instanceId = styleDoc.getString(INSTANCE_ID_FIELD);
                            if (styleDoc.containsKey(GLOBAL_WIDGET) && styleDoc.get(GLOBAL_WIDGET) != null && styleDoc.getBoolean(GLOBAL_WIDGET)) {
                                if (instanceId.startsWith("hf-")) {
                                    if (isThemeAssigned(styleDoc, theme, true, true)) {
                                        replaceStyleId(styleDoc, oldStyleId, newStyleId, true, true);
                                    }
                                } else {
                                    if (isThemeAssigned(styleDoc, theme, false, true)) {
                                        replaceStyleId(styleDoc, oldStyleId, newStyleId, false, true);
                                    }
                                }
                            } else {
                                if (isThemeAssigned(styleDoc, theme, false, false)) {
                                    replaceStyleId(styleDoc, oldStyleId, newStyleId, false, false);
                                }
                            }
                        }
                    }
                }
            }
            /*            Document contentQueryDoc = new Document(REFNUM, refNum);
            List<Document> orQueries = Arrays.asList(
                    new Document("content", new Document("$regex", oldStyleId)),
                    new Document("mobile_content", new Document("$regex", oldStyleId))
            );
            contentQueryDoc.put("$or", orQueries);
            List<Document> styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CONTENT, db, contentQueryDoc);
            for (Document styleDoc : styleDocs) {
                styleDoc.remove("_id");
                String instanceId = styleDoc.getString(INSTANCE_ID_FIELD);

                if (styleDoc.containsKey(GLOBAL_WIDGET) && styleDoc.get(GLOBAL_WIDGET) != null && styleDoc.getBoolean(GLOBAL_WIDGET)) {
                    if (instanceId.startsWith("hf-")) {
                        if (isThemeAssigned(styleDoc, theme, true, true)) {
                            replaceStyleIdInContent(styleDoc, oldStyleId, newStyleId, true, true);
                        }
                    } else {
                        if (isThemeAssigned(styleDoc, theme, false, true)) {
                            replaceStyleIdInContent(styleDoc, oldStyleId, newStyleId, false, true);
                        }
                    }
                } else {
                    if (isThemeAssigned(styleDoc, theme, false, false)) {
                        replaceStyleIdInContent(styleDoc, oldStyleId, newStyleId, false, false);
                    }
                }
            }*/
            timer.schedule(new TimerTask() {

                @Override
                public void run() {
                    logger.info("style metadata generation started at {}", new Date());
                    logger.info("for refNum {}", refNum);
                    generateStylesMetadata(refNum);
                }
            }, 3000);
            logger.info("scheduled styles metadata generation task!");
            return replaceStyleUsageCaas(refNum, oldStyleId, newStyleId, theme);
        } catch (Exception e) {
            logger.error("error while replacing style usage", e);
            return false;
        }
    }

    /*    public void replaceStyleIdInContent(Document styleDoc, String oldStyleId, String newStyleId, boolean isHF, boolean isGlobalWidget) {
        try {
            logger.info("replaceStyleIdInContent started for styleDoc: {}, oldStyleId: {}, newStyleId: {}", styleDoc, oldStyleId, newStyleId);
            String refNum = styleDoc.getString(REFNUM);
            String instanceId = styleDoc.getString(INSTANCE_ID_FIELD);
            Document updateDoc = new Document();
            if (styleDoc.containsKey("content") && styleDoc.get("content") != null) {
                String content = styleDoc.getString("content");
                content = content.replaceAll(oldStyleId, newStyleId);
                updateDoc.put("content", content);
            }
            if (styleDoc.containsKey("mobile_content") && styleDoc.get("mobile_content") != null) {
                String content = styleDoc.getString("mobile_content");
                content = content.replaceAll(oldStyleId, newStyleId);
                updateDoc.put("mobile_content", content);
            }

            if (!updateDoc.isEmpty()) {
                styleDoc.remove("content");
                styleDoc.remove("mobile_content");
                mongoManager.upsert(styleDoc, new Document("$set", updateDoc), Constants.CANVAS_SITE_CONTENT, db);

                if (!isGlobalWidget && !isHF) {
                    String locale = styleDoc.getString(LOCALE);
                    String pageId = styleDoc.getString(PAGE_ID);
                    makeWidgetInstanceDirty(refNum, locale, pageId, instanceId);

                } else if (isGlobalWidget && !isHF) {
                    String locale = styleDoc.getString(LOCALE);
                    setGlobalWidgetHasEditTrue(refNum, locale, instanceId);

                } else if (isGlobalWidget && isHF) {
                    String locale = styleDoc.getString(LOCALE);
                    setPageHFHasEditTrue(refNum, locale, instanceId);
                }
            }
        } catch (Exception e) {
            logger.error("Error in replaceStyleIdInContent: ", e);
            throw e;
        }
    }*/
    public boolean replaceStyleUsageCaas(String refNum, String oldStyleId, String newStyleId, String theme) {
        try {
            logger.info("replaceStyleUsageCaas for refNum: {}, oldStyleId: {}, newStyleId: {}", refNum, oldStyleId, newStyleId);
            String replaceStyletUrl = conf.getConfig("template.api").getString("base");
            if (replaceStyletUrl == null) {
                throw new PhenomException("template.api is not configured in conf file..");
            }
            replaceStyletUrl = replaceStyletUrl + "canvas/replaceStyleUsageCaas";
            Map<String, Object> params = new HashMap<>();
            params.put(REFNUM, refNum);
            params.put("oldStyleId", oldStyleId);
            params.put("newStyleId", newStyleId);
            params.put("theme", theme);
            JsonNode response = remoteServiceCaller.sendPostSync(replaceStyletUrl, params);
            logger.info("response from replaceStyleUsageCaas for {} is {}", params, response);
            if (response != null && response.has("status") && (response.get("status").asText()).equalsIgnoreCase(SUCCESS)) {
                return true;
            } else {
                logger.error(" Error from template service for replaceStyleUsageCaas : {}", response);
                return false;
            }
        } catch (Exception ex) {
            logger.error("Exception in replaceStyleUsageCaas {}", ex);
            return false;
        }
    }

    public void replaceStyleId(Document styleDoc, String oldStyleId, String newStyleId, boolean isHF, boolean isGlobalWidget) {
        try {
            logger.info("replaceStyleId started for styleDoc: {}, oldStyleId: {}, newStyleId: {}", styleDoc, oldStyleId, newStyleId);
            String refNum = styleDoc.getString(REFNUM);
            String instanceId = styleDoc.getString(INSTANCE_ID_FIELD);
            Map<String, Object> settingsMap = (Map<String, Object>) styleDoc.get("settings");
            replaceStyleId(settingsMap, oldStyleId, newStyleId);
            Set<String> styleIdsSet = new HashSet<>();
            widgetUtil.extractStyleIds(settingsMap, styleIdsSet);
            Document updateDoc = new Document("settings", settingsMap).append(STYLE_IDS, styleIdsSet);
            styleDoc.remove("settings");
            styleDoc.remove(STYLE_IDS);
            mongoManager.upsert(styleDoc, new Document("$set", updateDoc), CANVAS_SITE_INSTANCE_SETTINGS, db);
            if (!isGlobalWidget && !isHF) {
                String locale = styleDoc.getString(LOCALE);
                String pageId = styleDoc.getString(PAGE_ID);
                makeWidgetInstanceDirty(refNum, locale, pageId, instanceId);
            } else if (isGlobalWidget && !isHF) {
                setGlobalWidgetHasEditTrue(refNum, null, instanceId);
            } else if (isGlobalWidget && isHF) {
                String locale = styleDoc.getString(LOCALE);
                setPageHFHasEditTrue(refNum, locale, instanceId);
                updateHFPublishStatus(refNum, locale, instanceId);
            }
        } catch (Exception e) {
            logger.error("Error in replaceStyleId: ", e);
            throw e;
        }
    }

    public void replaceStyleId(Map<String, Object> settingsMap, String oldStyleId, String newStyleId) {
        for (String dataPs : settingsMap.keySet()) {
            if (dataPs.equalsIgnoreCase("cards")) {
                List<Map<String, Object>> cards = (List<Map<String, Object>>) settingsMap.get("cards");
                for (Map<String, Object> card : cards) {
                    if (card != null && !card.isEmpty()) {
                        replaceStyleId(card, oldStyleId, newStyleId);
                    }
                }
            } else if (settingsMap.get(dataPs) != null) {
                if (settingsMap.get(dataPs) instanceof String) {
                    if (oldStyleId.equals(settingsMap.get(dataPs))) {
                        settingsMap.put(dataPs, newStyleId);
                    }
                } else {
                    Map<String, Object> objData = (Map<String, Object>) settingsMap.get(dataPs);
                    if (objData.containsKey(STYLE_ID) && objData.get(STYLE_ID) != null) {
                        if (oldStyleId.equals(objData.get(STYLE_ID))) {
                            objData.put(STYLE_ID, newStyleId);
                        }
                    }
                }
            }
        }
    }

    public boolean isThemeAssigned(Document styleDoc, String theme, boolean isHF, boolean isGlobalWidget) {
        try {
            logger.info("isThemeAssigned started for styleDoc: {}, theme: {}", styleDoc, theme);
            if (!isGlobalWidget && !isHF) {
                logger.info("page doc!");
                String locale = styleDoc.getString(LOCALE);
                String pageId = styleDoc.getString(PAGE_ID);
                String refNum = styleDoc.getString(REFNUM);
                return isThemeAssigned(refNum, locale, pageId, theme);
            } else if (isGlobalWidget && !isHF) {
                logger.info("globalwidget doc!");
                String refNum = styleDoc.getString(REFNUM);
                String globalWidgetInstance = styleDoc.getString(INSTANCE_ID_FIELD);
                Document query = new Document(REFNUM, refNum).append(INSTANCE_ID_FIELD, globalWidgetInstance);
                List<Document> instanceDocs = mongoManager.findAllDocuments(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, query);
                for (Document instanceDoc : instanceDocs) {
                    String locale = instanceDoc.getString(LOCALE);
                    String pageId = instanceDoc.getString(PAGE_ID);
                    if (isThemeAssigned(refNum, locale, pageId, theme)) {
                        return true;
                    }
                }
            } else if (isGlobalWidget && isHF) {
                logger.info("hf doc!");
                String refNum = styleDoc.getString(REFNUM);
                String hfInstance = styleDoc.getString(INSTANCE_ID_FIELD);
                Document query = new Document(REFNUM, refNum).append("hfInstanceId", hfInstance);
                List<Document> instanceDocs = mongoManager.findAllDocuments(CANVAS_SITE_HF_PAGE_ASSIGNMENTS, db, query);
                for (Document instanceDoc : instanceDocs) {
                    String locale = instanceDoc.getString(LOCALE);
                    String pageId = instanceDoc.getString(PAGE_ID);
                    if (isThemeAssigned(refNum, locale, pageId, theme)) {
                        return true;
                    }
                }
            }
        } catch (Exception e) {
            logger.error("Error in isThemeAssigned: ", e);
        }
        return false;
    }

    public void getStyleIdUsageV2(String refNum, String styleId, Map<String, Map<String, Object>> responseStructure, Set<String> instanceIds, String theme) {
        try {
            //          Set<String> newPageIdDocs = new HashSet<>();
            //          Set<String> hfPageDocs = new HashSet<>();
            //          Set<String> globalWidgetPageDocs = new HashSet<>();
            Document styleQuery = new Document(REFNUM, refNum).append(STYLE_ID, styleId);
            List<Document> styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db, styleQuery);
            Set<String> pageIdDocs = new HashSet<>();
            List<Document> globalWidgetDocs = new ArrayList<>();
            List<Document> hfDocs = new ArrayList<>();
            styleDocs.forEach(styleDoc -> {
                String instanceId = styleDoc.getString(INSTANCE_ID_FIELD);
                if (styleDoc.containsKey(GLOBAL_WIDGET) && styleDoc.get(GLOBAL_WIDGET) != null && styleDoc.getBoolean(GLOBAL_WIDGET)) {
                    if (instanceId.startsWith("hf-")) {
                        hfDocs.add(styleDoc);
                    } else {
                        globalWidgetDocs.add(styleDoc);
                    }
                } else {
                    String locale = styleDoc.getString(LOCALE);
                    String siteVariant = styleDoc.getString(SITE_VARIANT);
                    String pageId = styleDoc.getString(PAGE_ID);
                    String uniqueInstanceId = refNum + "%" + locale + "%" + siteVariant + "%" + pageId + "%" + instanceId;
                    pageIdDocs.add(uniqueInstanceId);
                }
            });
            logger.info("pageIdDocs before are: {}", pageIdDocs);
            Set<String> newPageIdDocs = getStyleIdUsageCount(refNum, theme, pageIdDocs).get();
            logger.info("pageIdDocs are: {}", newPageIdDocs);
            Set<String> hfPageDocs = getHFStyleIdUsageCount(refNum, theme, hfDocs).get();
            logger.info("hfPageDocs are: {}", hfPageDocs);
            Set<String> globalWidgetPageDocs = getGlobalWidgetStyleIdUsageCount(refNum, theme, globalWidgetDocs).get();
            logger.info("globalWidgetPageDocs are: {}", globalWidgetPageDocs);
            instanceIds.addAll(newPageIdDocs);
            instanceIds.addAll(hfPageDocs);
            instanceIds.addAll(globalWidgetPageDocs);
            constructResponse(responseStructure, newPageIdDocs);
            constructResponse(responseStructure, hfPageDocs);
            constructResponse(responseStructure, globalWidgetPageDocs);
        } catch (Exception e) {
            logger.info("failed to fetch style usage data due to {}", e);
        }
    }

    public void getStyleIdUsageV3(String refNum, String styleId, Map<String, Map<String, Object>> responseStructure, Set<String> instanceIds, String theme) {
        try {
            //          Set<String> newPageIdDocs = new HashSet<>();
            //          Set<String> hfPageDocs = new HashSet<>();
            //          Set<String> globalWidgetPageDocs = new HashSet<>();
            Set<String> uniqueSavedDeletedInstances = new HashSet<>();
            Set<String> pageIdDocs = new HashSet<>();
            List<Document> globalWidgetDocs = new ArrayList<>();
            List<Document> hfDocs = new ArrayList<>();
            List<String> collections = Arrays.asList(Constants.CANVAS_SITE_INSTANCE_SETTINGS);
            for (String collection : collections) {
                Document styleQuery = new Document(REFNUM, refNum).append(STYLE_IDS, styleId);
                if (collection.equalsIgnoreCase(Constants.CANVAS_SITE_CONTENT)) {
                    styleQuery.remove(STYLE_IDS);
                    //                    List<Document> orQueries = new ArrayList<>();
                    //                    orQueries.add(new Document("content", new Document("$regex", styleId)));
                    //                    orQueries.add(new Document("mobile_content", new Document("$regex", styleId)));
                    //                    styleQuery.put("$or", orQueries);
                    String styleIdSelector = STYLE_ID_STRING.replace(STYLE_ID_NAME, styleId);
                    styleQuery.put("$text", new org.bson.Document("$search", styleIdSelector));
                }
                List<Document> styleDocs = mongoManager.findAllDocuments(collection, db, styleQuery);
                styleDocs.forEach(styleDoc -> {
                    String instanceId = styleDoc.getString(INSTANCE_ID_FIELD);
                    if (styleDoc.containsKey(GLOBAL_WIDGET) && styleDoc.get(GLOBAL_WIDGET) != null && styleDoc.getBoolean(GLOBAL_WIDGET)) {
                        if (instanceId.startsWith("hf-")) {
                            hfDocs.add(styleDoc);
                        } else {
                            globalWidgetDocs.add(styleDoc);
                        }
                    } else {
                        String locale = styleDoc.getString(LOCALE);
                        String siteVariant = styleDoc.getString(SITE_VARIANT);
                        String pageId = styleDoc.getString(PAGE_ID);
                        String uniqueInstanceId = refNum + "%" + locale + "%" + siteVariant + "%" + pageId + "%" + instanceId;
                        pageIdDocs.add(uniqueInstanceId);
                    }
                });
            }
            logger.info("pageIdDocs before are: {}", pageIdDocs);
            Set<String> newPageIdDocs = getStyleIdUsageCount(refNum, theme, pageIdDocs).get();
            logger.info("pageIdDocs are: {}", newPageIdDocs);
            Set<String> hfPageDocs = getHFStyleIdUsageCount(refNum, theme, hfDocs).get();
            logger.info("hfPageDocs are: {}", hfPageDocs);
            Set<String> globalWidgetPageDocs = getGlobalWidgetStyleIdUsageCount(refNum, theme, globalWidgetDocs).get();
            logger.info("globalWidgetPageDocs are: {}", globalWidgetPageDocs);
            Document stylesQuery = new Document(REFNUM, refNum).append(STYLE_IDS, styleId);
            List<String> widgetCollections = Arrays.asList(Constants.CANVAS_SAVED_WIDGETS, Constants.CANVAS_DELETED_WIDGETS);
            for (String collection : widgetCollections) {
                List<Document> styleDocs = mongoManager.findAllDocuments(collection, db, stylesQuery);
                styleDocs.forEach(styleDoc -> {
                    String instanceId;
                    if (collection.equalsIgnoreCase(CANVAS_SAVED_WIDGETS)) {
                        instanceId = styleDoc.getString(WIDGET_ID_FIELD);
                    } else {
                        instanceId = styleDoc.getString("activityId");
                    }
                    String uniqueInstanceId = refNum + "%" + instanceId;
                    uniqueSavedDeletedInstances.add(uniqueInstanceId);
                });
            }
            instanceIds.addAll(newPageIdDocs);
            instanceIds.addAll(hfPageDocs);
            instanceIds.addAll(globalWidgetPageDocs);
            instanceIds.addAll(uniqueSavedDeletedInstances);
            constructResponse(responseStructure, newPageIdDocs);
            constructResponse(responseStructure, hfPageDocs);
            constructResponse(responseStructure, globalWidgetPageDocs);
            logger.info("getStyleUsageCaas for refNum: {}, styleId: {}, theme: {}", refNum, styleId, theme);
            String styleUsageUrl = conf.getConfig("template.api").getString("base");
            if (styleUsageUrl == null) {
                throw new PhenomException("template.api is not configured in conf file..");
            }
            styleUsageUrl = styleUsageUrl + "canvas/getInnerWidgetsStyleIdUsage";
            Map<String, Object> params = new HashMap<>();
            params.put(REFNUM, refNum);
            params.put("styleId", styleId);
            params.put("theme", theme);
            JsonNode response = remoteServiceCaller.sendPostSync(styleUsageUrl, params);
            logger.info("response from getInnerWidgetsStyleIdUsage for {} is {}", params, response);
            if (response != null && response.has("status") && (response.get("status").asText()).equalsIgnoreCase(SUCCESS)) {
                Map<String, Object> data = Json.fromJson(response.get("data"), Map.class);
                List<String> uniqueSavedInstances;
                List<String> uniquePageSpecificDocs;
                uniqueSavedInstances = (List<String>) data.get("uniqueSavedInstances");
                uniquePageSpecificDocs = (List<String>) data.get("uniquePageSpecificDocs");
                instanceIds.addAll(uniqueSavedInstances);
                constructResponse(responseStructure, new HashSet<>(uniquePageSpecificDocs));
            } else {
                logger.error(" Error from template service for getInnerWidgetsStyleIdUsage : {}", response);
            }
        } catch (Exception e) {
            logger.info("failed to fetch style v3 usage data due to {}", e);
        }
    }

    public void constructResponse(Map<String, Map<String, Object>> responseStructure, Set<String> pageIdDocs) {
        pageIdDocs.forEach(pageIdDoc -> {
            String[] list = pageIdDoc.split("%");
            String locale = list[1];
            String siteVariant = list[2];
            String pageId = list[3];
            if (!responseStructure.containsKey(locale)) {
                Map<String, Object> variantPageMap = new HashMap<>();
                Set<String> pageIds = new HashSet<>();
                variantPageMap.put(siteVariant, pageIds);
                responseStructure.put(locale, variantPageMap);
            } else if (responseStructure.containsKey(locale) && !responseStructure.get(locale).containsKey(siteVariant)) {
                Set<String> pageIds = new HashSet<>();
                responseStructure.get(locale).put(siteVariant, pageIds);
            }
            ((Set) responseStructure.get(locale).get(siteVariant)).add(pageId);
        });
    }

    public CompletableFuture<Set<String>> getStyleIdUsageCount(String refNum, String theme, Set<String> pageDocs) {
        return CompletableFuture.supplyAsync(() -> pageDocs.stream().filter(item -> validateItem(refNum, theme, item)).collect(Collectors.toSet()));
    }

    public boolean validateItem(String refNum, String theme, String item) {
        String[] list = item.split("%");
        String locale = list[1];
        String pageId = list[3];
        return isThemeAssigned(refNum, locale, pageId, theme);
    }

    public CompletableFuture<Set<String>> getGlobalWidgetStyleIdUsageCount(String refNum, String theme, List<Document> globalWidgetDocs) {
        return CompletableFuture.supplyAsync(() -> {
            Set<String> uniqueInstances = new HashSet<>();
            globalWidgetDocs.forEach(hfDoc -> {
                String globalWidgetInstance = hfDoc.getString(INSTANCE_ID_FIELD);
                uniqueInstances.add(globalWidgetInstance);
            });
            return getGlobalWidgetStyleIdData(refNum, theme, uniqueInstances);
        });
    }

    public CompletableFuture<Set<String>> getHFStyleIdUsageCount(String refNum, String theme, List<Document> hfDocs) {
        return CompletableFuture.supplyAsync(() -> {
            Set<String> uniqueInstances = new HashSet<>();
            hfDocs.forEach(hfDoc -> {
                String hfInstance = hfDoc.getString(INSTANCE_ID_FIELD);
                uniqueInstances.add(hfInstance);
            });
            return getHFStyleIdData(refNum, theme, uniqueInstances);
        });
    }

    public Set<String> getHFStyleIdData(String refNum, String theme, Set<String> uniqueIds) {
        try {
            Set<String> pageDocs = new HashSet<>();
            uniqueIds.forEach(id -> {
                Document query = new Document(REFNUM, refNum).append("hfInstanceId", id);
                List<Document> instanceDocs = mongoManager.findAllDocuments(CANVAS_SITE_HF_PAGE_ASSIGNMENTS, db, query);
                instanceDocs.forEach(doc -> {
                    String locale = doc.getString(LOCALE);
                    String pageId = doc.getString(PAGE_ID);
                    String siteVariant = doc.getString(SITE_VARIANT);
                    String uniqueInstanceId = refNum + "%" + locale + "%" + siteVariant + "%" + pageId + "%" + id;
                    pageDocs.add(uniqueInstanceId);
                });
            });
            logger.info("hfPageDocs before are: {}", pageDocs);
            return getStyleIdUsageCount(refNum, theme, pageDocs).get();
        } catch (Exception e) {
            logger.info("Error in getting hf usage data: {}", e);
            return null;
        }
    }

    public Set<String> getGlobalWidgetStyleIdData(String refNum, String theme, Set<String> uniqueIds) {
        try {
            Set<String> pageDocs = new HashSet<>();
            uniqueIds.forEach(id -> {
                Document query = new Document(REFNUM, refNum).append(INSTANCE_ID_FIELD, id);
                List<Document> instanceDocs = mongoManager.findAllDocuments(CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, query);
                instanceDocs.forEach(doc -> {
                    String locale = doc.getString(LOCALE);
                    String siteVariant = doc.getString(SITE_VARIANT);
                    String pageId = doc.getString(PAGE_ID);
                    String uniqueInstanceId = refNum + "%" + locale + "%" + siteVariant + "%" + pageId + "%" + id;
                    pageDocs.add(uniqueInstanceId);
                });
            });
            logger.info("globalWidgetPageDocs before are: {}", pageDocs);
            return getStyleIdUsageCount(refNum, theme, pageDocs).get();
        } catch (Exception e) {
            logger.info("Error in fetching global widget usage data: {}", e);
            return null;
        }
    }

    public boolean isThemeAssigned(String refNum, String locale, String pageId, String theme) {
        Document themeQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(PAGE_ID, pageId);
        logger.info("query is : {}", themeQuery);
        Document themeDoc = mongoManager.findDocument(CANVAS_SITE_THEME_ASSIGNMENTS, db, themeQuery);
        if (themeDoc != null) {
            return themeDoc.getString(THEME_ID).equalsIgnoreCase(theme);
        }
        return theme.equalsIgnoreCase(DEFAULT);
    }

    public Map<String, Object> duplicateStyle(String refNum, String tag, String displayName, String deviceType, String theme, String fromStyleId, String duplicateStyleId) {
        Map<String, Object> response = new HashMap<>();
        try {
            String dbName = conf.getString(MONGO_DB);
            String currentEnv = conf.getString(SRC_ENV);
            String styleId = (duplicateStyleId != null) ? duplicateStyleId : "phw-g-" + siteUtil.normalize(displayName);
            Document querydoc = new Document(REFNUM, refNum);
            querydoc.put(TAG, tag);
            querydoc.put(THEME, theme);
            querydoc.put(STYLE_ID, fromStyleId);
            List<Document> orQueries = new ArrayList<>();
            orQueries.add(new Document(DISPLAY_NAME, new Document("$regex", "^" + displayName + "$").append("$options", "i")));
            orQueries.add(new Document(STYLE_ID, styleId));
            List<Document> queryDocs = new ArrayList<>();
            if ("prod".equalsIgnoreCase(currentEnv) || "cmsqa1".equalsIgnoreCase(currentEnv)) {
                Document lowerEnvsiteStyleQuery = new Document(REFNUM, refNum);
                lowerEnvsiteStyleQuery.append("$or", orQueries);
                lowerEnvsiteStyleQuery.append(THEME, theme);
                List<Document> lowerEnvdocs = preProdMongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, preprodDb, lowerEnvsiteStyleQuery);
                if (!lowerEnvdocs.isEmpty()) {
                    logger.error("Styles already exist with {} for {} and {} tag in lower env for {}", displayName, refNum, tag, currentEnv);
                    response.put(STATUS_KEY, false);
                    response.put("message", "Please try creating style with different display name");
                    return response;
                }
            }
            Document siteStyleQuery = new Document(REFNUM, refNum);
            siteStyleQuery.append("$or", orQueries);
            siteStyleQuery.append(THEME, theme);
            Document globalStyleQuery = new Document(REFNUM, CANVUS_REFNUM);
            globalStyleQuery.append("$or", orQueries);
            queryDocs.add(globalStyleQuery);
            queryDocs.add(siteStyleQuery);
            List<Document> docs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, db, new Document("$or", queryDocs));
            if (!docs.isEmpty()) {
                logger.error("Styles already exist with {} for {} and {} tag", displayName, refNum, tag);
                response.put(STATUS_KEY, false);
                response.put("message", "Please try creating style with different display name");
                return response;
            }
            List<Document> styleDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, dbName, querydoc);
            if (styleDocs == null || styleDocs.isEmpty()) {
                Document globalQueryDoc = new Document(REFNUM, CANVUS_REFNUM);
                globalQueryDoc.put(STYLE_ID, fromStyleId);
                styleDocs = mongoManager.findAllDocuments(CANVAS_SITE_CUSTOM_STYLES, dbName, globalQueryDoc);
            }
            for (Document styleDoc : styleDocs) {
                styleDoc.remove(ID);
                styleDoc.put(THEME, theme);
                styleDoc.put(REFNUM, refNum);
                styleDoc.put(DISPLAY_NAME, displayName);
                String css = styleDoc.getString(CSS).replace(fromStyleId, styleId);
                styleDoc.put(CSS, css);
                styleDoc.put(STYLE_ID, styleId);
                styleDoc.put("savedStyle", true);
            }
            mongoManager.insertDocuments(styleDocs, CANVAS_SITE_CUSTOM_STYLES, dbName);
            addCustomStylesCssToPage(refNum, theme);
            logger.info("widget style/attribute usage data generation started at {}", new Date());
            List<String> devices = Arrays.asList("desktop", "mobile");
            devices.forEach(device -> {
                generateStyleAttributesMetadata(refNum, styleId, null, device, new HashMap<>(), theme);
            });
            response.put(STATUS_KEY, true);
            return response;
        } catch (Exception e) {
            logger.error("couldn't duplcaite style --> {}", e);
            response.put(STATUS_KEY, false);
        }
        return response;
    }

    public void handleCardHoverData(String refNum, String theme, String tag, String styleId, String deviceType, String displayName, String oldStyleId, boolean fromDetach, String cardBlockStyleId, Map<String, Object> cardBlockChilds, boolean generateCssWithId, boolean fromBuildStyles) {
        Document query = new Document(REFNUM, refNum);
        query.append(TAG, tag);
        query.append(STYLE_ID, oldStyleId);
        query.append(THEME, theme);
        if (tag.equalsIgnoreCase("phw-card-block")) {
            //TODO: Fetch all the childs which have cardHover styleDocs and insert new docs with new styleId
            //TODO: query for eachChild should be done with all the childs (which are coming from the widgetmetadata in the payload) and insert new docs with new cardBlock styleId
            List<String> styleIds = new ArrayList<>();
            for (String eachCardBlockChild : cardBlockChilds.keySet()) {
                Map<String, Object> child = (Map<String, Object>) cardBlockChilds.get(eachCardBlockChild);
                styleIds.add((String) child.get("styleId"));
            }
            query.append("cardBlockStyleId", oldStyleId);
            query.remove(TAG);
            query.append(STYLE_ID, new Document("$in", styleIds));
            List<Document> styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
            styleDocs.forEach(eachDoc -> {
                eachDoc.remove(ID);
                eachDoc.put("cardBlockStyleId", styleId);
                String css = eachDoc.getString("css");
                css = css.replace(oldStyleId, styleId);
                eachDoc.append(CSS, css);
            });
            if (!styleDocs.isEmpty()) {
                mongoManager.insertDocuments(styleDocs, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
            }
        } else if (cardBlockStyleId != null) {
            //TODO: query with old style id and card block styleId and inser docs with new styleId
            query.append("cardBlockStyleId", cardBlockStyleId);
            query.append("state", "cardHover");
            List<Document> styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
            styleDocs.forEach(eachDoc -> {
                eachDoc.remove(ID);
                eachDoc.put(STYLE_ID, styleId);
                eachDoc.put(DISPLAY_NAME, displayName);
                String css = eachDoc.getString("css");
                css = css.replace(oldStyleId, styleId);
                eachDoc.append(CSS, css);
            });
            if (!styleDocs.isEmpty()) {
                mongoManager.insertDocuments(styleDocs, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
            }
        }
    }

    public void copyOldGlobalStylesDataForOtherDevices(String refNum, String theme, String tag, String styleId, String deviceType, String displayName, String oldStyleId, boolean fromDetach, boolean generateCssWithId, boolean fromBuildStyles) {
        Document query = new Document(REFNUM, refNum);
        query.append(TAG, tag);
        query.append(STYLE_ID, oldStyleId);
        query.append(THEME, theme);
        query.append("state", new Document("$ne", "cardHover"));
        List<Document> styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        styleDocs.forEach(eachDoc -> {
            eachDoc.remove(ID);
            Map<String, Object> siteStyleJson = new HashMap<>();
            if (!oldStyleId.startsWith("phw-g-i-")) {
                Document masterQuery = new Document(REFNUM, CANVUS_REFNUM);
                masterQuery.put(STYLE_ID, oldStyleId);
                masterQuery.put(TAG, tag);
                masterQuery.put(DEVICE_TYPE, eachDoc.getString(DEVICE_TYPE));
                if (eachDoc.getString("state") != null) {
                    masterQuery.put("state", eachDoc.getString("state"));
                }
                org.bson.Document masterDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, masterQuery);
                Map<String, Object> masterStyleJson = new HashMap<>();
                if (masterDoc != null) {
                    masterStyleJson = (Map<String, Object>) masterDoc.get("styles");
                }
                if (!masterStyleJson.isEmpty()) {
                    siteStyleJson.putAll(masterStyleJson);
                }
            }
            eachDoc.put(STYLE_ID, styleId);
            eachDoc.put(DISPLAY_NAME, displayName);
            siteStyleJson.putAll((Map<String, Object>) eachDoc.get("styles"));
            String state = null;
            if (eachDoc.get("state") != null) {
                state = eachDoc.getString("state");
                //                query.put("state", eachDoc.getString("state"));
            } else {
                //                query.remove("state");
            }
            Map<String, Object> visibilitySettings = new HashMap<>();
            Map<String, Object> overlaySettings = new HashMap<>();
            if (eachDoc.get(VISIBILITY_SETTINGS) != null) {
                visibilitySettings = (Map<String, Object>) eachDoc.get(VISIBILITY_SETTINGS);
            }
            if (eachDoc.get(OVERLAY_SETTINGS) != null) {
                overlaySettings = (Map<String, Object>) eachDoc.get(OVERLAY_SETTINGS);
            }
            String css = constructCss(siteStyleJson, styleId, tag, state, eachDoc.getString(DEVICE_TYPE), visibilitySettings, overlaySettings, null, refNum, generateCssWithId, theme, oldStyleId, fromBuildStyles);
            eachDoc.put("styles", siteStyleJson);
            eachDoc.put("css", css);
            if (fromDetach) {
                eachDoc.remove("savedStyle");
            }
            //            query.put(STYLE_ID, styleId);
            //            query.put(DEVICE_TYPE, eachDoc.getString(DEVICE_TYPE));
            //            mongoManager.upsert(query, eachDoc, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        });
        mongoManager.insertDocuments(styleDocs, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        copyCanvasMissingDocs(refNum, theme, tag, styleId, displayName, oldStyleId, generateCssWithId, fromBuildStyles);
    }

    public void copyCanvasMissingDocs(String refNum, String theme, String tag, String styleId, String displayName, String oldStyleId, boolean generateCssWithId, boolean fromBuildStyles) {
        if (!oldStyleId.startsWith("phw-g-i-")) {
            Document canvasQuery = new Document(REFNUM, CANVAS_REFNUM);
            canvasQuery.append(TAG, tag);
            canvasQuery.append(STYLE_ID, oldStyleId);
            List<Document> canvasSpecificDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, canvasQuery);
            canvasSpecificDocs.forEach(eachDoc -> {
                eachDoc.remove(ID);
                Document canvasSiteQuery = new Document(canvasQuery);
                canvasSiteQuery.put(REFNUM, refNum);
                canvasSiteQuery.put(THEME, theme);
                canvasSiteQuery.put(DEVICE_TYPE, eachDoc.getString(DEVICE_TYPE));
                if (eachDoc.getString("state") != null) {
                    canvasSiteQuery.put("state", eachDoc.getString("state"));
                } else {
                    canvasSiteQuery.put("state", new Document("$exists", false));
                }
                if (!mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_CUSTOM_STYLES, db, canvasSiteQuery)) {
                    /**
                     * for class specific styles.
                     *                     default style --> site specific
                     *                     custom style  --> master json.
                     *                     priority is to site specific class.
                     *                     handling  this case.
                     *                     while copying removing styles that are there in default style if it is site specific
                     */
                    org.bson.Document defaultSettingsDoc = mongoManager.findDocument("canvas_tag_default_settings", db, new Document());
                    List<String> tagDefaultClasses = new ArrayList<>();
                    if (defaultSettingsDoc != null && defaultSettingsDoc.containsKey("tagDefaultClasses") && defaultSettingsDoc.get("tagDefaultClasses") != null) {
                        tagDefaultClasses = (List<String>) defaultSettingsDoc.get("tagDefaultClasses");
                    }
                    if (tagDefaultClasses.contains(eachDoc.getString(TAG))) {
                        Document siteSpeficDefaultQuery = new Document(canvasSiteQuery);
                        siteSpeficDefaultQuery.put(STYLE_ID, DEFAULT);
                        Document defaultDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, siteSpeficDefaultQuery);
                        if (defaultDoc != null && !defaultDoc.isEmpty()) {
                            Map<String, Object> defaultStyles = (Map<String, Object>) defaultDoc.get("styles", Map.class);
                            Map<String, Object> siteStyles = (Map<String, Object>) eachDoc.get("styles", Map.class);
                            defaultStyles.forEach((key, value) -> {
                                siteStyles.remove(key);
                            });
                            eachDoc.put("styles", siteStyles);
                        }
                    }
                    eachDoc.put(REFNUM, refNum);
                    eachDoc.put(THEME, theme);
                    eachDoc.put(STYLE_ID, styleId);
                    eachDoc.put(DISPLAY_NAME, displayName);
                    String state = null;
                    if (eachDoc.get("state") != null) {
                        state = eachDoc.getString("state");
                    }
                    String css = constructCss((Map<String, Object>) eachDoc.get("styles"), styleId, tag, state, eachDoc.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), null, refNum, generateCssWithId, theme, oldStyleId, fromBuildStyles);
                    eachDoc.put("styles", eachDoc.get("styles"));
                    eachDoc.put("css", css);
                    mongoManager.insertDocument(eachDoc, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
                }
            });
        }
    }

    public Map<String, Object> dragDropSuperWidget(SuperWidgetElements superWidgetElement) {
        Map<String, Object> response = new HashMap<>();
        try {
            logger.info("placing superwidget in page with payload: {}", superWidgetElement);
            List<String> devices = superWidgetElement.getTargetDevice().equalsIgnoreCase(MOBILE) ? Arrays.asList(MOBILE) : Arrays.asList(DESKTOP, MOBILE);
            String html = superWidgetElement.getStructure();
            String instanceId = SiteUtil.generateUniqueId();
            Element widgetEle = widgetUtil.getCanvasWidget(html);
            widgetEle.attr(INSTANCE_ID_FIELD, instanceId);
            html = widgetEle.toString();
            logger.info("final superwidget ele html is: {}", html);
            for (String device : devices) {
                String pageKey = SiteUtil.constructPhPageKey(superWidgetElement.getRefNum(), device, superWidgetElement.getLocale(), superWidgetElement.getPageId());
                String pageContent = redisManager.get(pageKey);
                Page page = Json.fromJson(Json.parse(pageContent), Page.class);
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
                /*String html = superWidgetElement.getStructure();*/
                boolean placeContent = false;
                if (superWidgetElement.getNextSiblingId() != null && !placeContent) {
                    Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, superWidgetElement.getNextSiblingId());
                    if (!elements.isEmpty()) {
                        Element element = elements.get(0);
                        element.before(html);
                        placeContent = true;
                    }
                }
                if (superWidgetElement.getPreviousSiblingId() != null && !placeContent) {
                    Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, superWidgetElement.getPreviousSiblingId());
                    if (!elements.isEmpty()) {
                        Element element = elements.get(0);
                        element.after(html);
                        placeContent = true;
                    }
                }
                if (superWidgetElement.getParentElementId() != null && superWidgetElement.isAddLast() && !placeContent) {
                    Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, superWidgetElement.getParentElementId());
                    if (!elements.isEmpty()) {
                        Element element = elements.get(0);
                        element.append(html);
                        placeContent = true;
                    }
                }
                if (superWidgetElement.getParentElementId() != null && !placeContent) {
                    Elements elements = pageDoc.getElementsByAttributeValue(DATA_ATTRIBUTE_ID, superWidgetElement.getParentElementId());
                    if (!elements.isEmpty()) {
                        Element element = elements.get(0);
                        element.prepend(html);
                        placeContent = true;
                    }
                }
                if (!placeContent) {
                    logger.error("Unknown place to add new superwidget content as NextSiblingId, PreviousSiblingId and ParentElementId are nulls");
                    response.put(STATUS_KEY, false);
                    return response;
                }
                page.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(page).toString());
            }
            response.put(STATUS_KEY, true);
            response.put("processedHtml", widgetEle.toString());
            response.put("instanceId", instanceId);
            return response;
        } catch (Exception e) {
            logger.info("Exception occurred while placing superwidget in page: {}", e);
        }
        response.put(STATUS_KEY, false);
        return response;
    }

    public void addCalculatedValuesToDevices(String refNum, String theme, String tag, String styleId, String state, String deviceType, String propertyValue, String propertyName, String displayName, boolean savedStyle, boolean fromDetach, String cardBlockStyleId, boolean generateCssWithId, boolean fromBuildStyles) {
        try {
            Map<String, Object> data = new HashMap<>();
            Map<String, Object> visibilitySettings = new HashMap<>();
            Map<String, Object> overlaySettings = new HashMap<>();
            boolean removeCalculatedfontSize = false;
            boolean textStyle = false;
            if (deviceType.equalsIgnoreCase("mobile")) {
                String mobileDefaultFontSize = getDeviceSpecificFontSize(refNum, theme, "mobile");
                if (propertyValue.startsWith("var(")) {
                    propertyValue = "max(calc(" + propertyValue + "*0.5),var(--mobile-font-size))";
                } else if (Integer.valueOf(propertyValue.replace("px", "")) < Integer.valueOf(mobileDefaultFontSize.replace("px", ""))) {
                    //                    removeCalculatedFontSize(refNum,theme, tag, styleId,"mobile");
                    removeCalculatedfontSize = true;
                    //                    return;
                } else {
                    int mobileValue = (int) (Integer.valueOf(propertyValue.replace("px", "")) * 0.5);
                    if (mobileValue < Integer.valueOf(mobileDefaultFontSize.replace("px", ""))) {
                        mobileValue = Integer.valueOf(mobileDefaultFontSize.replace("px", ""));
                    }
                    propertyValue = mobileValue + "px";
                }
            }
            if (deviceType.equalsIgnoreCase("tab")) {
                String tabDefaultFontSize = getDeviceSpecificFontSize(refNum, theme, "tab");
                if (propertyValue.startsWith("var(")) {
                    propertyValue = "max(calc(" + propertyValue + "*0.75),var(--tab-font-size))";
                } else if (Integer.valueOf(propertyValue.replace("px", "")) < Integer.valueOf(tabDefaultFontSize.replace("px", ""))) {
                    //                    removeCalculatedFontSize(refNum,theme, tag, styleId,"tab");
                    //                    return;
                    removeCalculatedfontSize = true;
                } else {
                    int tabValue = (int) (Integer.valueOf(propertyValue.replace("px", "")) * 0.75);
                    if (tabValue < Integer.valueOf(tabDefaultFontSize.replace("px", ""))) {
                        tabValue = Integer.valueOf(tabDefaultFontSize.replace("px", ""));
                    }
                    propertyValue = tabValue + "px";
                }
                //                propertyValue = tabValue + "px";
            }
            Document query = new Document(REFNUM, refNum);
            Document activityResp = new Document();
            query.append(TAG, tag);
            query.append(DEVICE_TYPE, deviceType);
            query.append(STYLE_ID, styleId);
            query.append(THEME, theme);
            if (state == null) {
                query.put("state", new Document("$exists", false));
            } else {
                query.put("state", state);
                if (state.equalsIgnoreCase("cardHover")) {
                    query.append("cardBlockStyleId", cardBlockStyleId);
                }
            }
            Document styleDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
            if (styleDoc == null) {
                if (removeCalculatedfontSize) {
                    return;
                }
                styleDoc = new Document(query);
                styleDoc.put(DISPLAY_NAME, displayName);
                if (state != null) {
                    styleDoc.put("state", state);
                } else {
                    styleDoc.remove("state");
                }
                if (!removeCalculatedfontSize) {
                    data.put(propertyName, propertyValue);
                    data.put("act-p-" + propertyName, propertyValue);
                }
            } else {
                data = (Map<String, Object>) styleDoc.get("styles");
                if (data.containsKey(propertyName) && !data.containsKey("act-p-" + propertyName)) {
                    return;
                }
                if (removeCalculatedfontSize && propertyName.equalsIgnoreCase("font-size") && data.containsKey("act-p-" + propertyName)) {
                    data.remove(propertyName);
                    data.remove("act-p-" + propertyName);
                } else {
                    data.put(propertyName, propertyValue);
                    data.put("act-p-" + propertyName, propertyValue);
                }
                if (styleDoc.containsKey(VISIBILITY_SETTINGS)) {
                    visibilitySettings = (Map<String, Object>) styleDoc.get(VISIBILITY_SETTINGS);
                }
                if (styleDoc.containsKey(OVERLAY_SETTINGS)) {
                    overlaySettings = (Map<String, Object>) styleDoc.get(OVERLAY_SETTINGS);
                }
                if (styleDoc.containsKey("textStyle")) {
                    textStyle = styleDoc.getBoolean("textStyle");
                }
            }
            styleDoc.remove(ID);
            String css = constructCss(data, styleId, tag, state, deviceType, visibilitySettings, overlaySettings, cardBlockStyleId, refNum, generateCssWithId, theme, null, fromBuildStyles);
            styleDoc.append("styles", data);
            styleDoc.append("css", css);
            if (textStyle) {
                styleDoc.append("textStyle", textStyle);
            }
            if (savedStyle) {
                styleDoc.append("savedStyle", savedStyle);
            }
            if (state != null) {
                query.append("state", state);
            } else {
                query.put("state", new Document("$exists", false));
            }
            if (styleDoc.containsKey(DISPLAY_NAME)) {
                styleDoc.append(DISPLAY_NAME, styleDoc.getString(DISPLAY_NAME));
            }
            if (visibilitySettings != null && visibilitySettings.size() > 0) {
                styleDoc.append(VISIBILITY_SETTINGS, visibilitySettings);
            }
            if (!overlaySettings.isEmpty() && tag.equalsIgnoreCase(PHW_IMG_CTR)) {
                styleDoc.append(OVERLAY_SETTINGS, overlaySettings);
            }
            if (isCanvasMigratedSite(refNum, Optional.empty()) && (!fromBuildStyles || (fromBuildStyles && styleId.startsWith("phw-g-i-")))) {
                styleDoc.append(EDITED_STYLES, getChangedCssProperties(refNum, tag, deviceType, styleId, theme, state, cardBlockStyleId, data));
            }
            styleDoc.append(THEME, theme);
            activityResp.put("newValue", styleDoc);
            if (fromDetach) {
                styleDoc.remove("savedStyle");
            }
            mongoManager.upsert(query, new Document("$set", styleDoc), CANVAS_SITE_CUSTOM_STYLES, db);
        } catch (Exception e) {
            logger.error("couldn't add");
        }
    }

    public org.bson.Document canvasWidgetIds(String refNum, List<String> widgetIds, List<Map<String, String>> aureliaWidgetIds) {
        org.bson.Document resp = new org.bson.Document();
        try {
            for (String widgetId : widgetIds) {
                try {
                    if (widgetId == null)
                        continue;
                    org.bson.Document queryDoc = new org.bson.Document();
                    org.bson.Document global_queryDoc = new org.bson.Document();
                    org.bson.Document mongoDoc = null;
                    queryDoc.append(WIDGET_ID_FIELD, widgetId);
                    global_queryDoc.append("globalWidgetId", widgetId);
                    if (mongoManager.checkIfDocumentExists("canvas_savedwidgets", conf.getString(MONGO_DB), queryDoc)) {
                        queryDoc.append(REFNUM, refNum);
                        mongoDoc = mongoManager.findDocument("canvas_savedwidgets", conf.getString(MONGO_DB), queryDoc);
                    }
                    if (mongoDoc == null && mongoManager.checkIfDocumentExists("canvas_sitewidgetpanel", conf.getString(MONGO_DB), queryDoc)) {
                        queryDoc.append(REFNUM, refNum);
                        mongoDoc = mongoManager.findDocument("canvas_sitewidgetpanel", conf.getString(MONGO_DB), queryDoc);
                        if (mongoDoc != null && mongoDoc.getString("panelDisplayName") != null) {
                            resp.put(widgetId, mongoDoc.getString("panelDisplayName"));
                            continue;
                        } else {
                            mongoDoc = null;
                            queryDoc.remove(REFNUM);
                        }
                    }
                    if (mongoDoc == null && mongoManager.checkIfDocumentExists("canvas_site_globalwidgetpanel", conf.getString(MONGO_DB), global_queryDoc)) {
                        mongoDoc = mongoManager.findDocument("canvas_site_globalwidgetpanel", conf.getString(MONGO_DB), global_queryDoc);
                    }
                    if (mongoDoc == null && preProdMongoManager.checkIfDocumentExists("canvas_globalwidgets", preprodDb, queryDoc)) {
                        mongoDoc = preProdMongoManager.findDocument("canvas_globalwidgets", preprodDb, queryDoc);
                    }
                    if (mongoDoc == null && mongoManager.checkIfDocumentExists("canvas_migrated_aurelia_globalWidgets", conf.getString(MONGO_DB), global_queryDoc)) {
                        mongoDoc = mongoManager.findDocument("canvas_migrated_aurelia_globalWidgets", conf.getString(MONGO_DB), global_queryDoc);
                    }
                    if (mongoDoc == null) {
                        logger.info("not found widget doc for widgetId {} with refNum {} and doc is {}", widgetId, refNum, mongoDoc);
                        continue;
                    }
                    if (mongoDoc == null && (mongoDoc.getString("displayName") == null || mongoDoc.getString("name") == null || mongoDoc.getString("panelDisplayName") == null)) {
                        logger.info("not found widget doc for widgetId {} with refNum {} and doc is {}", widgetId, refNum, mongoDoc);
                        continue;
                    } else {
                        if (mongoDoc.getString("name") != null) {
                            resp.put(widgetId, mongoDoc.getString("name"));
                        } else if (mongoDoc.getString("displayName") != null) {
                            resp.put(widgetId, mongoDoc.getString("displayName"));
                        } else {
                            resp.put(widgetId, mongoDoc.getString(("panelDisplayName")));
                        }
                    }
                } catch (Exception e) {
                    logger.error("excetion while fetching widget names {}", e);
                }
            }
            if (!aureliaWidgetIds.isEmpty()) {
                for (Map<String, String> widgetData : aureliaWidgetIds) {
                    try {
                        String widgertId = widgetData.get("widgetId");
                        Document queryDoc = new Document();
                        queryDoc.put("viewName", widgetData.get("viewName"));
                        queryDoc.put("version", widgetData.get("version"));
                        queryDoc.put("themeName", widgetData.get("themeName").split("-")[0]);
                        queryDoc.put("widgetName", widgetData.get("widgetName"));
                        Document resultDoc = null;
                        resultDoc = mongoManager.findDocument("widgetinfo", conf.getString(MONGO_DB), queryDoc);
                        if (resultDoc != null) {
                            resp.put(widgertId, resultDoc.getString("displayName"));
                        } else {
                            queryDoc.remove("themeName");
                            resultDoc = mongoManager.findDocument("widgetinfo", conf.getString(MONGO_DB), queryDoc);
                            if (resultDoc != null) {
                                resp.put(widgertId, resultDoc.getString("displayName"));
                            } else {
                                logger.info("not found widget doc for widgetId {} with theme {} with refNum {} and doc is {}", widgertId, widgetData.get("themeName"), refNum, resultDoc);
                            }
                        }
                    } catch (Exception e) {
                        logger.error("excetion while fetching widget names {}", e);
                    }
                }
            }
            return resp;
        } catch (Exception e) {
            logger.error("excetion while fetching widget names {}", e);
        }
        return resp;
    }

    public boolean generateStylesMetadata(String refNum) {
        try {
            logger.info("Generation of styles metadata started for refNum {}", refNum);
            Document query = new Document(REFNUM, refNum);
            List<Document> settingsDocs = mongoManager.findAllDocuments(CANVAS_SITE_INSTANCE_SETTINGS, db, query);
            for (Document doc : settingsDocs) {
                List<String> styleIds = new ArrayList<>();
                if (doc.containsKey("settings") && doc.get("settings") != null) {
                    String locale = doc.getString(LOCALE);
                    String siteVariant = doc.getString(SITE_VARIANT);
                    String targetDevice = doc.getString(DEVICE_TYPE);
                    String instanceId = doc.getString(INSTANCE_ID_FIELD);
                    Document deleteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append(DEVICE_TYPE, targetDevice).append(INSTANCE_ID_FIELD, instanceId);
                    logger.info("Deleting styles metadata doc {}", deleteQuery);
                    mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
                    Map<String, Object> settings = doc.get("settings", Map.class);
                    Set<String> styleIdsSet = new HashSet<>();
                    widgetUtil.extractStyleIds(settings, styleIdsSet);
                    styleIds.addAll(styleIdsSet);
                    logger.info("styleIds for {} are: {}", doc, styleIds);
                    styleIds.forEach(styleId -> {
                        Document insertDoc = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append("device", targetDevice).append(INSTANCE_ID_FIELD, instanceId).append(STYLE_ID, styleId);
                        if (doc.containsKey(GLOBAL_WIDGET) && doc.get(GLOBAL_WIDGET) != null && doc.getBoolean(GLOBAL_WIDGET)) {
                            insertDoc.append(GLOBAL_WIDGET, true);
                        } else {
                            insertDoc.append(PAGE_ID, doc.getString(PAGE_ID));
                        }
                        logger.info("Inserting styles metadata doc {}", insertDoc);
                        mongoManager.insertDocument(insertDoc, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
                    });
                }
            }
            return true;
        } catch (Exception e) {
            logger.error("Error in generating styles metadata for site {}", e);
        }
        return false;
    }

    public void deleteWidgetMetadata(CanvasDeleteWidgetRequest canvasDeleteRequest) {
        logger.info("Delete widget meta data started for request {}", canvasDeleteRequest);
        String refNum = canvasDeleteRequest.getRefNum();
        String locale = canvasDeleteRequest.getLocale();
        String siteVariant = canvasDeleteRequest.getSiteVariant();
        String pageId = canvasDeleteRequest.getPageId();
        String instanceId = canvasDeleteRequest.getInstanceId();
        String deviceMode = canvasDeleteRequest.getDeviceMode();
        Document deleteQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append(SITE_VARIANT, siteVariant).append(DEVICE_TYPE, deviceMode).append(INSTANCE_ID_FIELD, instanceId);
        if (!canvasDeleteRequest.isGlobalWidget()) {
            deleteQuery.append(PAGE_ID, pageId);
        } else {
            deleteQuery.append(GLOBAL_WIDGET, true);
        }
        logger.info("Deleting netadata docs from collection {} for query {}", CANVAS_SITE_INSTANCE_SETTINGS_METADATA, deleteQuery);
        mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_INSTANCE_SETTINGS_METADATA, db);
        logger.info("Deleting docs from collection {} for query {}", CANVAS_SITE_INSTANCE_SETTINGS, deleteQuery);
        mongoManager.deleteDocuments(deleteQuery, CANVAS_SITE_INSTANCE_SETTINGS, db);
    }

    public void addStyleIds(List<String> styleIds, Map<String, Object> settings) {
        logger.info("Adding styleIds to process..");
        settings.forEach((key, value) -> {
            if (key.equals("cards")) {
                if (value != null) {
                    List<Map<String, Object>> cardSettings = (List<Map<String, Object>>) settings.get("cards");
                    cardSettings.forEach(data -> {
                        data.forEach((cardKey, cardValue) -> {
                            if (cardValue != null && cardValue.toString().length() > 0) {
                                styleIds.add(cardValue.toString());
                            }
                        });
                    });
                }
            } else if (value != null && value.toString().length() > 0) {
                styleIds.add(value.toString());
            }
        });
    }

    public Document renameStyle(String refNum, String tag, String styleId, String displayName, String theme, Document response) {
        Document query = new Document(REFNUM, refNum);
        query.append(TAG, tag);
        query.append(STYLE_ID, styleId);
        query.append(THEME, theme);
        String currentEnv = conf.getString(SRC_ENV);
        String dbName = conf.getString(MONGO_DB);
        Document displayNameQuery = new Document(query);
        displayNameQuery.put(DISPLAY_NAME, displayName);
        if ("prod".equalsIgnoreCase(currentEnv) || "cmsqa1".equalsIgnoreCase(currentEnv)) {
            List<Document> lowerEnvdocs = preProdMongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, preprodDb, displayNameQuery);
            if (!lowerEnvdocs.isEmpty()) {
                logger.error("Styles already exist with {} for {} and {} tag in lower env for {}", displayName, refNum, currentEnv);
                response.put(STATUS_KEY, false);
                response.put("message", "Please try creating style with different display name.");
                return response;
            }
        }
        if (mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_CUSTOM_STYLES, dbName, displayNameQuery)) {
            logger.error("same displayName already exists --> {}", displayNameQuery);
            response.put(STATUS_KEY, false);
            response.put("message", "Display name already exists");
            return response;
        }
        List<Document> styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        if (styleDocs == null || styleDocs.isEmpty()) {
            Document canvusQuery = new Document(REFNUM, CANVAS_REFNUM);
            canvusQuery.put(TAG, tag);
            canvusQuery.put(STYLE_ID, styleId);
            styleDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, canvusQuery);
        }
        styleDocs.forEach(styleDoc -> {
            response.put("previousValue", styleDoc.get(DISPLAY_NAME).toString());
            response.put("newValue", displayName);
            Document queryDoc = new Document(styleDoc);
            styleDoc.put(DISPLAY_NAME, displayName);
            if (styleDoc.getString(REFNUM).equalsIgnoreCase(CANVAS_REFNUM)) {
                styleDoc.put(REFNUM, refNum);
                styleDoc.put(TYPE, "system");
                styleDoc.put("styles", new HashMap<>());
                styleDoc.put("theme", theme);
                styleDoc.remove(ID);
                //                String css = constructCss(new HashMap<>(), styleId, tag, styleDoc.getString("state"),
                //                        styleDoc.getString("device"), new HashMap<>(), new HashMap<>(), null, refNum);
                //                styleDoc.put("css", css);
                queryDoc.put(REFNUM, refNum);
                queryDoc.put("theme", theme);
                queryDoc.remove(ID);
            }
            mongoManager.upsert(queryDoc, new Document("$set", styleDoc), CANVAS_SITE_CUSTOM_STYLES, db);
        });
        updateThemePublishStatus(refNum, theme);
        logger.info("scheduled attribute usage data generation task for updateStyle!");
        response.put(STATUS_KEY, SUCCESS);
        return response;
    }

    public void addIfDesktopDocIsMissing(Document styleDoc, boolean generateCssWithId, boolean fromBuildStyles) {
        try {
            if (!styleDoc.getString(DEVICE_TYPE).equalsIgnoreCase(DESKTOP)) {
                Document desktopStyleDoc = new Document(REFNUM, styleDoc.getString(REFNUM));
                desktopStyleDoc.put(THEME, styleDoc.getString(THEME));
                desktopStyleDoc.put(TAG, styleDoc.getString(TAG));
                desktopStyleDoc.put(STYLE_ID, styleDoc.getString(STYLE_ID));
                desktopStyleDoc.put(DEVICE_TYPE, DESKTOP);
                String dbName = conf.getString(MONGO_DB);
                if (!mongoManager.checkIfDocumentExists(CANVAS_SITE_CUSTOM_STYLES, dbName, desktopStyleDoc)) {
                    desktopStyleDoc.put(DISPLAY_NAME, styleDoc.getString(DISPLAY_NAME));
                    desktopStyleDoc.put("styles", new HashMap<>());
                    desktopStyleDoc.put("css", constructCss(new HashMap<>(), styleDoc.getString(STYLE_ID), styleDoc.getString(TAG), styleDoc.getString("state"), styleDoc.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), null, styleDoc.getString(REFNUM), generateCssWithId, styleDoc.getString(THEME), null, fromBuildStyles));
                    mongoManager.insertDocument(desktopStyleDoc, CANVAS_SITE_CUSTOM_STYLES, dbName);
                }
            }
        } catch (Exception e) {
            logger.error("couldn't addIfDesktopDocIsMissing --> {}", e);
        }
    }

    public boolean saveSuperWidgetContentInPage(SuperWidgetElements superWidgetElements) {
        try {
            List<String> devices = superWidgetElements.getTargetDevice().equalsIgnoreCase(MOBILE) ? Arrays.asList(MOBILE) : Arrays.asList(DESKTOP, MOBILE);
            for (String device : devices) {
                String pageKey = SiteUtil.constructPhPageKey(superWidgetElements.getRefNum(), device, superWidgetElements.getLocale(), superWidgetElements.getPageId());
                String pageContent = redisManager.get(pageKey);
                Page page = Json.fromJson(Json.parse(pageContent), Page.class);
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
                Map<String, Object> instanceIdContentMap = superWidgetElements.getInstanceContentMapping();
                logger.info("super widget instance to content map is: {}", instanceIdContentMap);
                for (Map.Entry<String, Object> entry : instanceIdContentMap.entrySet()) {
                    String key = entry.getKey();
                    Object value = entry.getValue();
                    Elements elements = pageDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, key);
                    logger.info("Super widget elements are: {}", elements);
                    if (!elements.isEmpty()) {
                        Element element = elements.get(0);
                        if (MOBILE.equalsIgnoreCase(device)) {
                            if (MOBILE.equalsIgnoreCase(superWidgetElements.getTargetDevice())) {
                                element.attr("isMobileEdited", true);
                            } else if (DESKTOP.equalsIgnoreCase(superWidgetElements.getTargetDevice())) {
                                if (element.hasAttr("isMobileEdited")) {
                                    element.attr("isMobileEdited", true);
                                    continue;
                                }
                            }
                        }
                        String html = value.toString();
                        Element div = element.getElementById("blog-cke");
                        logger.info("super widget content html is: {}", html);
                        logger.info("Super widget content div is: {}", div);
                        if (div != null) {
                            div.html(html);
                        }
                    }
                }
                page.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(page).toString());
            }
            return true;
        } catch (Exception e) {
            logger.info("Exception occurred during saveSuperWidgetContentInPage: {}", e);
        }
        return false;
    }

    public void syncDesignSettingsWithDesktop(String refNum, String locale, String siteVariant, String pageId, String instanceId, String device) {
        try {
            Document query = new Document(REFNUM, refNum);
            query.put(LOCALE, locale);
            query.put(SITE_VARIANT, siteVariant);
            query.put(PAGE_ID, pageId);
            query.put(DEVICE_TYPE, DESKTOP);
            query.put(INSTANCE_ID_FIELD, instanceId);
            Document siteInstanceDoc = mongoManager.findDocument(Constants.CANVAS_SITE_INSTANCE_SETTINGS, db, query);
            removeMobileFieldInInstanceDoc((Map<String, Object>) siteInstanceDoc.get("settings"));
            logger.info("settings -->{}", siteInstanceDoc.get("settings"));
            Set<String> styleIdsSet = new HashSet<>();
            widgetUtil.extractStyleIds((Map<String, Object>) siteInstanceDoc.get("settings"), styleIdsSet);
            siteInstanceDoc.append(STYLE_IDS, styleIdsSet);
            mongoManager.upsert(query, new Document("$set", siteInstanceDoc), Constants.CANVAS_SITE_INSTANCE_SETTINGS, db);
        } catch (Exception e) {
            logger.error("Failed syncDesignSettingsWithDesktop --> {}", e);
        }
    }

    public void removeMobileFieldInInstanceDoc(Map<String, Object> settings) {
        try {
            settings.forEach((dataPs, designSettings) -> {
                if (dataPs.equalsIgnoreCase("cards")) {
                    List<Map<String, Object>> designSettingsCards = (List<Map<String, Object>>) designSettings;
                    designSettingsCards.forEach(card -> {
                        removeMobileFieldInInstanceDoc(card);
                    });
                } else {
                    if (!(designSettings instanceof String)) {
                        Map<String, Map<String, Object>> designSettingsMap = (Map<String, Map<String, Object>>) designSettings;
                        if (designSettingsMap.containsKey("elementVisibility")) {
                            designSettingsMap.get("elementVisibility").remove("mobile");
                        }
                        if (designSettingsMap.containsKey("overlaySettings")) {
                            designSettingsMap.get("overlaySettings").remove("mobile");
                        }
                        if (designSettingsMap.containsKey("additionalSettings") && designSettingsMap.get("additionalSettings").containsKey("itemsPerRow")) {
                            Map<String, Object> itemsPerRow = (Map<String, Object>) designSettingsMap.get("additionalSettings").get("itemsPerRow");
                            List<String> classes = (List<String>) itemsPerRow.get("classes");
                            if (classes != null) {
                                classes = classes.stream().filter(eachCls -> !eachCls.startsWith("phw-grid-sm-")).collect(Collectors.toList());
                                itemsPerRow.put("classes", classes);
                                designSettingsMap.get("additionalSettings").put("itemsPerRow", itemsPerRow);
                            }
                        }
                    }
                }
            });
        } catch (Exception e) {
            logger.error("Failed removeMobileFieldInInstanceDoc --> {}", e);
        }
    }

    public void addOrRemoveDeviceOverriddenAttr(String refNum, String locale, String pageId, String instanceId, String device, String type) {
        if (!device.equalsIgnoreCase("mobile")) {
            return;
        }
        List<String> devices = Arrays.asList("desktop", "mobile");
        devices.forEach(deviceType -> {
            String pageKey = "ph:page:" + refNum + ":" + deviceType + ":" + locale + ":" + pageId;
            String pageValue = redisManager.get(pageKey);
            if (pageValue != null) {
                Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                String pageHtml = p.getPageHtml();
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageHtml);
                List<Element> sectionEles = pageDoc.getElementsByTag(SECTION);
                if (sectionEles != null) {
                    for (Element sectionEle : sectionEles) {
                        if (sectionEle.hasAttr(CANVAS_STATIC_WIDGET_ATTR) && sectionEle.hasAttr(INSTANCE_ID_FIELD) && sectionEle.attr(INSTANCE_ID_FIELD).equalsIgnoreCase(instanceId)) {
                            if (type.equalsIgnoreCase("add")) {
                                sectionEle.attr(SYNC_WITH_DESKTOP, "false");
                            } else if (type.equalsIgnoreCase("remove")) {
                                sectionEle.attr(SYNC_WITH_DESKTOP, "true");
                            }
                        }
                    }
                }
                p.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(p).toString());
            }
        });
    }

    public Boolean syncSuperWidgetContent(String refNum, String locale, String siteVariant, String pageId, String instanceId) {
        try {
            List<String> devices = Arrays.asList("desktop", "mobile");
            boolean superWidgetFoundInDesktop = false;
            Element superWidgetElm = null;
            for (String device : devices) {
                String pageKey = SiteUtil.constructPhPageKey(refNum, device, locale, pageId);
                String pageContent = redisManager.get(pageKey);
                Page page = Json.fromJson(Json.parse(pageContent), Page.class);
                org.jsoup.nodes.Document pageDoc = HtmlParser.parse(page.getPageHtml());
                List<Element> superWidgetElms = pageDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId);
                if (device.equalsIgnoreCase(DESKTOP) && superWidgetElms != null && !superWidgetElms.isEmpty()) {
                    superWidgetElm = superWidgetElms.get(0);
                    superWidgetElm.removeAttr("isMobileEdited");
                    superWidgetFoundInDesktop = true;
                }
                if (device.equalsIgnoreCase(MOBILE)) {
                    if (superWidgetElms != null && !superWidgetElms.isEmpty() && superWidgetFoundInDesktop && superWidgetElm != null) {
                        Element mobileSuperWidgetElm = superWidgetElms.get(0);
                        mobileSuperWidgetElm.html(superWidgetElm.getElementById(SUPER_WIDGET_ID).toString());
                        mobileSuperWidgetElm.removeAttr("isMobileEdited");
                        logger.debug("mobile -->{}", mobileSuperWidgetElm);
                    }
                }
                page.setPageHtml(pageDoc.toString());
                redisManager.set(pageKey, Json.toJson(page).toString());
            }
        } catch (Exception e) {
            logger.error("couldn't syncSuperWidgetContent --> {}", e);
            return false;
        }
        return true;
    }

    public Document getCanvasViewDocumentByWidgetId(String widgetId) {
        Document query = new Document(WIDGET_ID_FIELD, widgetId);
        query.append(LATEST, true);
        Document widgetDoc = preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETS, preprodDb, query);
        if (widgetDoc == null) {
            logger.error("Could not find widget doc for {}", widgetId);
            return new Document();
        }
        Document viewQuery = new Document(LATEST, true);
        viewQuery.append(VIEW_ID, widgetDoc.getString(VIEW_ID));
        return preProdMongoManager.findDocument(CANVAS_GLOBALWIDGETVIEWS, preprodDb, viewQuery);
    }

    public void updateCanvasScreenShotImage(String refNum, String widgetId, boolean isMobile, String screenshotS3Url) {
        logger.info("In storeCanvasWidgetScreenshotInMongo img - {}", screenshotS3Url);
        try {
            String coll = "canvas_widgetviewimages";
            Document query = new Document();
            query.put(REFNUM, refNum);
            query.put(WIDGET_ID_FIELD, widgetId);
            Document updateDoc = new Document();
            if (!isMobile) {
                updateDoc.put("desktop", screenshotS3Url);
            } else {
                updateDoc.put("mobile", screenshotS3Url);
            }
            preProdMongoManager.upsert(query, new Document("$set", updateDoc), coll, preprodDb);
        } catch (Exception e) {
            logger.error("Error in storeCanvasWidgetScreenshotInMongo - {}", e);
        }
    }

    public void markDefaultStyleIdsAsSaved(String refNum) {
        String mongoDb = conf.getString(MONGO_DB);
        Document tagDefaultSettings = mongoManager.findDocument(CANVAS_TAG_DEFAULT_SETTINGS, mongoDb, new Document());
        List<String> styleIds = (List<String>) tagDefaultSettings.get(DEFAULT_SAVED_STYLEIDS, List.class);
        Document queryDoc = new Document(REFNUM, refNum);
        queryDoc.put(TAG, "phw-btn");
        queryDoc.put(STYLE_ID, new Document("$in", styleIds));
        mongoManager.upsert(queryDoc, new Document("$set", new Document("savedStyle", true)), CANVAS_SITE_CUSTOM_STYLES, mongoDb);
    }

    public void addIfNormalStateDocIsMissing(Document query, String displayName, boolean savedStyle, boolean fromDetach, boolean generateCssWithId, boolean fromBuildStyles) {
        query.remove("cardBlockStyleId");
        query.put("state", new Document("$exists", false));
        query.put("device", DESKTOP);
        if (!mongoManager.checkIfDocumentExists(CANVAS_SITE_CUSTOM_STYLES, db, query)) {
            Document updateDoc = new Document(query);
            updateDoc.remove("state");
            updateDoc.put(DISPLAY_NAME, displayName);
            updateDoc.put("styles", new HashMap<>());
            if (savedStyle) {
                updateDoc.put("savedStyle", savedStyle);
            }
            updateDoc.put("css", constructCss(new HashMap<>(), query.getString(STYLE_ID), query.getString(TAG), null, query.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), null, query.getString(REFNUM), generateCssWithId, query.getString(THEME), null, fromBuildStyles));
            if (fromDetach) {
                updateDoc.remove("savedStyle");
            }
            mongoManager.upsert(query, new Document("$set", updateDoc), Constants.CANVAS_SITE_CUSTOM_STYLES, db);
        }
    }

    public void addDefaultFontSizes(Map<String, Object> masterData, String group, String refNum, String theme, boolean retain) {
        for (String fontsizeName : masterData.keySet()) {
            Map<String, Object> fontsizeData = (Map<String, Object>) masterData.get(fontsizeName);
            Document doc = new Document(REFNUM, refNum);
            doc.put(GROUP, group);
            doc.put(THEME, theme);
            doc.put(DISPLAY_NAME, fontsizeData.get(DISPLAY_NAME).toString());
            doc.put(NAME, fontsizeData.get(NAME).toString());
            doc.put(VALUE, fontsizeData.get(VALUE).toString());
            if (fontsizeData.containsKey(IS_ICON_FONT)) {
                doc.put(IS_ICON_FONT, (boolean) fontsizeData.get(IS_ICON_FONT));
            }
            if (retain) {
                Document existsQuery = new Document(REFNUM, refNum);
                existsQuery.put(NAME, fontsizeData.get(NAME));
                existsQuery.put(THEME, theme);
                existsQuery.put(GROUP, group);
                if (mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, existsQuery)) {
                    continue;
                }
            }
            mongoManager.insertDocument(doc, Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db);
        }
    }

    public boolean isSavedStyle(String refNum, String theme, String styleId) {
        try {
            Document queryDoc = new Document(REFNUM, refNum);
            queryDoc.put(THEME, theme);
            queryDoc.put(STYLE_ID, styleId);
            queryDoc.put("savedStyle", true);
            return mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_CUSTOM_STYLES, db, queryDoc);
        } catch (Exception e) {
            logger.error("couldn't checkifSavedStyle -->{}", e);
        }
        return false;
    }

    public String getDeviceSpecificFontSize(String refNum, String theme, String device) {
        try {
            Document queryDoc = new Document(REFNUM, refNum);
            queryDoc.put(THEME, theme);
            if (device.equalsIgnoreCase("mobile")) {
                queryDoc.put("name", "--mobile-font-size");
            } else if (device.equalsIgnoreCase("tab")) {
                queryDoc.put("name", "--tab-font-size");
            }
            Document deviceFontSizeDoc = mongoManager.findDocument(Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, queryDoc);
            if (deviceFontSizeDoc != null && !deviceFontSizeDoc.isEmpty()) {
                return deviceFontSizeDoc.getString(VALUE);
            } else {
                queryDoc.put(THEME, DEFAULT);
                queryDoc.put(REFNUM, CANVUS_REFNUM);
                deviceFontSizeDoc = mongoManager.findDocument(Constants.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, queryDoc);
                return (deviceFontSizeDoc != null && deviceFontSizeDoc.containsKey(VALUE)) ? deviceFontSizeDoc.getString(VALUE) : "18px";
            }
        } catch (Exception e) {
            logger.error("couldn't getDeviceSpecificFontSize -->{}", e);
        }
        return "18px";
    }

    public Map<String, Map<String, Object>> getCanvasGlobalWidgetsInSite(String refNum) {
        Map<String, Map<String, Object>> globalWidgetsMap = new HashMap<>();
        org.bson.Document query = new org.bson.Document();
        query.put(REFNUM, refNum);
        List<Document> globalWidgets = mongoManager.findAllDocuments(CANVAS_SITE_GLOBALWIDGET_PANEL, conf.getString(MONGO_DB), query);
        for (Document doc : globalWidgets) {
            Map<String, Object> globalWidgetData = new HashMap<>();
            if (doc != null) {
                String displayName = doc.getString(NAME);
                globalWidgetData.put(DISPLAY_NAME, displayName);
            }
            String publishedState = doc.getString(PUBLISHEDSTATE);
            if (publishedState != null) {
                globalWidgetData.put(PUBLISHEDSTATE, publishedState);
            } else {
                globalWidgetData.put(PUBLISHEDSTATE, UNPUBLISHED);
            }
            globalWidgetsMap.put(doc.getString(INSTANCE_ID_FIELD), globalWidgetData);
        }
        return globalWidgetsMap;
    }

    public boolean flipChildElements(String refNum, String locale, String pageId, String instanceId, String dataPs, int cardIndex, String flipType) {
        try {
            List<String> devices = Arrays.asList("desktop", "mobile");
            devices.forEach(device -> {
                String pageKey = RedisKeyUtil.getPageKey(refNum, device, locale, pageId);
                String pageValue = redisManager.get(pageKey);
                if (pageValue != null) {
                    Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                    String pageHtml = p.getPageHtml();
                    org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageHtml);
                    Element sectionEle = pageDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId).first();
                    if (sectionEle != null) {
                        int index = -1;
                        for (Element dataPsEl : sectionEle.getElementsByAttributeValue(DATA_PS, dataPs)) {
                            index++;
                            if (cardIndex != -1 && cardIndex != index) {
                                continue;
                            }
                            if (flipType.equalsIgnoreCase("horizontalFlip")) {
                                if (!dataPsEl.getElementsByClass("ph-row").isEmpty())
                                    siteUtil.swapChildren(dataPsEl.getElementsByClass("ph-row").get(0).child(0), dataPsEl.getElementsByClass("ph-row").get(0).child(1));
                            } else if (flipType.equalsIgnoreCase("verticalFlip")) {
                                if (!dataPsEl.getElementsByClass("ph-row").isEmpty())
                                    siteUtil.swapChildren(dataPsEl.getElementsByClass("ph-row").get(0).child(0), dataPsEl.getElementsByClass("ph-row").get(0).child(1));
                            }
                        }
                    }
                    p.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(p).toString());
                }
            });
        } catch (Exception e) {
            logger.error("couldn't flipChildElements -->{}", e);
        }
        return true;
    }

    public boolean removeOverlayCtr(String refNum, String locale, String pageId, String instanceId, String dataPs, int cardIndex) {
        try {
            List<String> devices = Arrays.asList("desktop", "mobile");
            devices.forEach(device -> {
                String pageKey = RedisKeyUtil.getPageKey(refNum, device, locale, pageId);
                String pageValue = redisManager.get(pageKey);
                if (pageValue != null) {
                    Page p = Json.fromJson(Json.parse(pageValue), Page.class);
                    String pageHtml = p.getPageHtml();
                    org.jsoup.nodes.Document pageDoc = HtmlParser.parse(pageHtml);
                    Element sectionEle = pageDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId).first();
                    if (sectionEle != null) {
                        int index = -1;
                        for (Element dataPsEl : sectionEle.getElementsByAttributeValue(DATA_PS, dataPs)) {
                            index++;
                            if (cardIndex != -1 && cardIndex != index) {
                                continue;
                            }
                            dataPsEl.getElementsByAttributeValue("component-content-key", "overlay").remove();
                        }
                    }
                    p.setPageHtml(pageDoc.toString());
                    redisManager.set(pageKey, Json.toJson(p).toString());
                }
            });
        } catch (Exception e) {
            logger.error("couldn't flipChildElements -->{}", e);
        }
        return true;
    }

    public void createAndUpdateStylesForCards(String refNum, String locale, String siteVariant, String pageId, String theme, String instanceId, List<String> globalWidgetsInstanceIds, Map<String, Object> cardStyles, Map<String, Object> instanceDatapsVsStyleId) {
        try {
            for (String cardIndex : cardStyles.keySet()) {
                List<Object> eachCardDataPs = (List<Object>) cardStyles.get(cardIndex);
                for (Object eachCardDataPsObj : eachCardDataPs) {
                    Map<String, Object> cardStyle = (Map<String, Object>) eachCardDataPsObj;
                    for (String dataPs : cardStyle.keySet()) createAndUpdateStyles(refNum, locale, siteVariant, pageId, theme, instanceId, dataPs, Integer.parseInt(cardIndex), globalWidgetsInstanceIds, (Map<String, Object>) cardStyle.get(dataPs), true, instanceDatapsVsStyleId);
                }
            }
        } catch (Exception e) {
            logger.error("Excepion in createAndUpdateStylesForCards refNum {} locale {} pageId {} instanceId {}", refNum, locale, pageId, instanceId);
        }
    }

    private String createInstanceSpecificStyleId() {
        final String CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
        final int ID_LENGTH = 6;
        StringBuilder text = new StringBuilder();
        Random random = new Random();
        for (int i = 0; i < ID_LENGTH; i++) {
            int index = random.nextInt(CHARACTERS.length());
            text.append(CHARACTERS.charAt(index));
        }
        return "phw-g-i-" + text + "-ds";
    }

    public Map<String, Object> getExistingSettings(String refNum, String locale, String siteVariant, String pageId, String instanceId, List<String> globalWidgetsInstanceIds) {
        Map<String, Object> settings = new HashMap<>();
        try {
            boolean isHF = instanceId.startsWith("hf-") ? true : false;
            Document query = getSettingsQuery(refNum, locale, siteVariant, pageId, instanceId, globalWidgetsInstanceIds.contains(instanceId), isHF);
            Document siteInstanceDoc = mongoManager.findDocument(Constants.CANVAS_SITE_INSTANCE_SETTINGS, db, query);
            if (siteInstanceDoc != null) {
                return siteInstanceDoc.get("settings", Map.class);
            }
        } catch (Exception e) {
            logger.error("Exception in getExistingSettings refNum {} locale {} siteVariant{}  pageID{} {}", refNum, locale, siteVariant, pageId, e);
        }
        return null;
    }

    public Optional<String> getStyleFromInstanceDatapsVsStyleId(Map<String, Object> instanceDatapsVsStyleId, String dataPs, boolean cardSpecific, int index) {
        Optional<String> styleId = Optional.empty();
        try {
            if (cardSpecific && instanceDatapsVsStyleId.containsKey(String.valueOf(index))) {
                for (Map.Entry<String, Object> setting : instanceDatapsVsStyleId.entrySet()) {
                    if (setting.getKey().equalsIgnoreCase(String.valueOf(index))) {
                        Map<String, String> settingMap = (Map<String, String>) setting.getValue();
                        if (settingMap.containsKey(dataPs))
                            return Optional.of(settingMap.get(dataPs));
                    }
                }
            }
            if (!cardSpecific) {
                for (Map.Entry<String, Object> setting : instanceDatapsVsStyleId.entrySet()) {
                    if (setting.getKey().equalsIgnoreCase(dataPs))
                        return Optional.of(setting.getValue().toString());
                }
            }
        } catch (Exception e) {
            logger.error("Exception in getStyleFromInstanceDatapsVsStyleId dataPs {}", dataPs, e);
        }
        logger.debug("dataPs {} ---> styleID {}", dataPs, styleId);
        return styleId;
    }

    public void updateClassListInAdditional(String instanceId, String dataPs, Map<String, Object> existingSettings, Map<String, Object> styles) {
        logger.debug("updateClassListInAdditional instanceId {} dataPs {}", instanceId, dataPs);
        try {
            List<String> classList = (List<String>) styles.get("classList");
            if (instanceId.startsWith("hf-") && styles.containsKey("classList") && !classList.isEmpty()) {
                Map<String, Object> addSettings = new HashMap<>();
                Map<String, Object> mgWidgetSettings = new HashMap<>();
                mgWidgetSettings.put("classes", classList);
                mgWidgetSettings.put("type", "migratedWidgetSettings");
                addSettings.put("migratedWidgetSettings", mgWidgetSettings);
                if (existingSettings.containsKey(dataPs)) {
                    if (existingSettings.get(dataPs) instanceof String) {
                        Map<String, Object> temp = new HashMap<>();
                        temp.put("styleId", existingSettings.get(dataPs));
                        temp.put("additionalSettings", addSettings);
                        existingSettings.put(dataPs, temp);
                    } else {
                        Map<String, Object> temp = (Map<String, Object>) existingSettings.get(dataPs);
                        if (temp.containsKey("additionalSettings")) {
                            Map<String, Object> addSettingsMap = (Map<String, Object>) temp.get("additionalSettings");
                            addSettingsMap.put("migratedWidgetSettings", mgWidgetSettings);
                            temp.put("additionalSettings", addSettingsMap);
                        } else {
                            temp.put("additionalSettings", addSettings);
                            existingSettings.put(dataPs, temp);
                        }
                        existingSettings.put(dataPs, temp);
                    }
                } else {
                    Map<String, Object> temp = new HashMap<>();
                    temp.put("additionalSettings", addSettings);
                    existingSettings.put(dataPs, temp);
                }
            }
        } catch (Exception e) {
            logger.error("Exception in updateClassListInAdditional instanceId {} dataPs {} ", instanceId, dataPs);
        }
    }

    public void createAndUpdateStyles(String refNum, String locale, String siteVariant, String pageId, String theme, String instanceId, String dataPs, int index, List<String> globalWidgetsInstanceIds, Map<String, Object> styles, boolean cardSpecific, Map<String, Object> instanceDatapsVsStyleId) {
        try {
            Optional<String> styleId = Optional.empty();
            if (styles.containsKey("styleId"))
                styleId = Optional.of(styles.get("styleId").toString());
            if (instanceDatapsVsStyleId != null && (styleId == null || !styleId.isPresent()))
                styleId = getStyleFromInstanceDatapsVsStyleId(instanceDatapsVsStyleId, dataPs, cardSpecific, index);
            if (styleId == null || !styleId.isPresent()) {
                styleId = Optional.of(createInstanceSpecificStyleId());
            }
            Map<String, Object> existingSettings = getExistingSettings(refNum, locale, siteVariant, pageId, instanceId, globalWidgetsInstanceIds);
            if (existingSettings == null)
                existingSettings = new HashMap<>();
            if (cardSpecific) {
                List<Map<String, Object>> cards = (List<Map<String, Object>>) existingSettings.get("cards");
                Map<String, Object> card = cards.get(index);
                card.put(dataPs, styleId.get());
            } else {
                if (existingSettings.containsKey(dataPs) && !(existingSettings.get(dataPs) instanceof String)) {
                    Map<String, Object> temp = (Map<String, Object>) existingSettings.get(dataPs);
                    temp.put("styleId", styleId.get());
                    existingSettings.put(dataPs, temp);
                } else {
                    existingSettings.put(dataPs, styleId.get());
                }
            }
            styles.put("styleId", styleId.get());
            String type = "static";
            if (styles.containsKey(TYPE))
                type = styles.get(TYPE).toString();
            updateClassListInAdditional(instanceId, dataPs, existingSettings, styles);
            updateWidgetStylesv2(refNum, locale, siteVariant, pageId, instanceId, existingSettings, globalWidgetsInstanceIds.contains(instanceId), instanceId.startsWith("hf-"), type, DESKTOP);
            if (styles.containsKey(DESKTOP) && !styles.isEmpty())
                updateStylev2(refNum, styles.get("tag").toString(), styleId.get(), (Map<String, Object>) styles.get(DESKTOP), styleId.get(), false, null, "desktop", null, new HashMap<>(), theme, new HashMap<>(), false, null, false, false, null, new HashMap<>());
            if (styles.containsKey(MOBILE) && !styles.isEmpty())
                updateStylev2(refNum, styles.get("tag").toString(), styleId.get(), (Map<String, Object>) styles.get(MOBILE), styleId.get(), false, null, "mobile", null, new HashMap<>(), theme, new HashMap<>(), false, null, false, false, null, new HashMap<>());
            if (styles.containsKey(TABLET)) {
                updateStylev2(refNum, styles.get("tag").toString(), styleId.get(), (Map<String, Object>) styles.get(TABLET), styleId.get(), false, null, "tab", null, new HashMap<>(), theme, new HashMap<>(), false, null, false, false, null, new HashMap<>());
            }
        } catch (Exception e) {
            logger.error("Exception in createAndUpdateStyles refNum {} locale {} siteVariant{}  pageID{} {}", refNum, locale, siteVariant, pageId, e);
        }
    }

    public List<String> getGlobalWidgetInstanceIds(String refNum, String locale, String siteVariant, String pageId) {
        List<String> instanceIds = new ArrayList<>();
        try {
            Document query = new Document();
            query.put(REFNUM, refNum);
            query.put(LOCALE, locale);
            query.put(SITE_VARIANT, siteVariant);
            query.put(PAGE_ID, pageId);
            List<Document> globalWidgetMetadata = mongoManager.findAllDocuments(Constants.CANVAS_SITE_GLOBAL_WIDGET_METADATA, db, query);
            for (Document doc : globalWidgetMetadata) {
                instanceIds.add(doc.getString(INSTANCE_ID_FIELD));
            }
        } catch (Exception e) {
            logger.error("Exception in getGlobalWidgetInstanceIds refNum {} locale {} siteVariant{}  pageID{} {}", refNum, locale, siteVariant, pageId, e);
        }
        return instanceIds;
    }

    public void bulkUpdateInlineStyles(JsonNode payload, Http.Request request) {
        try {
            String refNum = payload.get(REFNUM).asText();
            String locale = payload.get(LOCALE).asText();
            String siteVariant = payload.get(SITE_VARIANT).asText();
            String pageId = payload.get(PAGE_ID).asText();
            String theme = payload.get(THEME).asText();
            String currEnv = conf.getString(SRC_ENV);
            Map<String, Object> styles = Json.fromJson(payload.get("styles"), Map.class);
            boolean generateCssUrl = true;
            if (payload.hasNonNull("generateCssurl"))
                generateCssUrl = payload.get("generateCssurl").asBoolean();
            if (refNum == null || locale == null || siteVariant == null || pageId == null || theme == null || styles == null || styles.isEmpty()) {
                logger.error("Invalid payload for bulkUpdateInlineStyles refNum {} locale {} siteVariant{}  pageID{} {}", refNum, locale, siteVariant, pageId);
                return;
            }
            Map<String, Object> lowerEnvDatapsVsStyleId = new HashMap<>();
            Map<String, Object> getAllStyleIdsPayload = new HashMap<>();
            getAllStyleIdsPayload.put(REFNUM, refNum);
            getAllStyleIdsPayload.put(LOCALE, locale);
            getAllStyleIdsPayload.put(SITE_VARIANT, siteVariant);
            getAllStyleIdsPayload.put(PAGE_ID, pageId);
            if (Arrays.asList("cmsqa2", "pre-prod").contains(currEnv))
                getAllStyleIdsPayload.put(ENV, currEnv.equalsIgnoreCase("cmsqa2") ? "cmsqa1" : "prod");
            else
                getAllStyleIdsPayload.put(ENV, currEnv.equalsIgnoreCase(currEnv));
            String url = conf.getString("service.baseUrl").replace("{{service}}", "ds-migration") + "getStyleIdsForPage";
            JsonNode resp = siteUtil.sendPostSync(url, getAllStyleIdsPayload);
            if (resp != null && resp.has("data"))
                lowerEnvDatapsVsStyleId = Json.fromJson(resp.get("data"), Map.class);
            else {
                logger.error("bulkUpdateInlineStyles is resp {} ", resp);
            }
            List<String> globalWidgetInstanceIds = getGlobalWidgetInstanceIds(refNum, locale, siteVariant, pageId);
            logger.debug("bulkUpdateWidgetStyles started refNum {} locale {} siteVariant{}  pageID{} {}", refNum, locale, siteVariant, pageId);
            for (String instanceId : styles.keySet()) {
                Map<String, Object> eachInstanceIdSettings = (Map<String, Object>) styles.get(instanceId);
                for (String key : eachInstanceIdSettings.keySet()) {
                    Map<String, Object> eachInstanceIdSetting = (Map<String, Object>) eachInstanceIdSettings.get(key);
                    if (key.equalsIgnoreCase("cards"))
                        createAndUpdateStylesForCards(refNum, locale, siteVariant, pageId, theme, instanceId, globalWidgetInstanceIds, eachInstanceIdSetting, (Map<String, Object>) lowerEnvDatapsVsStyleId.get(instanceId));
                    else
                        createAndUpdateStyles(refNum, locale, siteVariant, pageId, theme, instanceId, key, 0, globalWidgetInstanceIds, eachInstanceIdSetting, false, (Map<String, Object>) lowerEnvDatapsVsStyleId.get(instanceId));
                }
            }
            logger.debug("css file generation refNum {} theme {}", refNum, theme);
            if (generateCssUrl)
                addCustomStylesCssToPage(refNum, theme);
            ((ObjectNode) payload).put("styles", Json.toJson(styles));
            siteUtil.canvasApplyToLowerEnvByEndpoint(payload, "bulkUpdateInlineStyles", request);
        } catch (Exception e) {
            logger.error("Exception in bulkUpdateWidgetStyles refNum {} locale {} siteVariant{}  pageID{} {}", payload, e);
        }
    }

    public void updateStylev2(String refNum, String tag, String styleId, Map<String, Object> data, String displayName, boolean textStyle, String state, String deviceType, String type, Map<String, Object> visibilitySettings, String theme, Map<String, Object> overlaySettings, boolean copyOldGlobalStyleData, String oldStyleId, boolean savedStyle, boolean fromDetach, String cardBlockStyleId, Map<String, Object> cardBlockChilds) {
        if (copyOldGlobalStyleData && oldStyleId != null) {
            copyOldGlobalStylesDataForOtherDevices(refNum, theme, tag, styleId, deviceType, displayName, oldStyleId, fromDetach, false, false);
            handleCardHoverData(refNum, theme, tag, styleId, deviceType, displayName, oldStyleId, fromDetach, cardBlockStyleId, cardBlockChilds, false, false);
        }
        org.bson.Document defaultSettingsDoc = mongoManager.findDocument("canvas_tag_default_settings", db, new Document());
        List<String> buttonStyleIds = (List<String>) defaultSettingsDoc.get("defaultSavedStyleIds", List.class);
        if (buttonStyleIds.contains(styleId)) {
            savedStyle = true;
        }
        Document query = new Document(REFNUM, refNum);
        query.append(TAG, tag);
        query.append(DEVICE_TYPE, deviceType);
        query.append(STYLE_ID, styleId);
        query.append(THEME, theme);
        if (state != null) {
            query.append("state", state);
            if (state.equalsIgnoreCase("cardHover")) {
                query.append("cardBlockStyleId", cardBlockStyleId);
            }
        } else {
            query.append("state", new Document("$exists", false));
        }
        Document styleDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
        if (styleDoc == null) {
            if (state != null) {
                query.append("state", state);
            } else {
                query.remove("state");
            }
            styleDoc = new Document(query);
        }
        styleDoc.remove(ID);
        if (isCanvasMigratedSite(refNum, Optional.empty())) {
            styleDoc.append(EDITED_STYLES, getChangedCssProperties(refNum, tag, deviceType, (null == oldStyleId ? styleId : oldStyleId), theme, state, cardBlockStyleId, data));
        }
        //TODO Handle card hover style generation
        String css = constructCss(data, styleId, tag, state, deviceType, visibilitySettings, overlaySettings, cardBlockStyleId, refNum, false, theme, null, false);
        if (!deviceType.equalsIgnoreCase("desktop") && data.containsKey("act-p-font-size")) {
            data.remove("act-p-font-size");
        }
        styleDoc.append("styles", data);
        styleDoc.append("css", css);
        if (textStyle) {
            styleDoc.append("textStyle", textStyle);
        }
        if (savedStyle) {
            styleDoc.append("savedStyle", savedStyle);
        }
        if (state != null) {
            query.append("state", state);
        } else {
            query.append("state", new Document("$exists", false));
        }
        if (displayName != null) {
            styleDoc.append(DISPLAY_NAME, displayName);
        }
        if (visibilitySettings != null && visibilitySettings.size() > 0) {
            styleDoc.append(VISIBILITY_SETTINGS, visibilitySettings);
        }
        if (!overlaySettings.isEmpty() && tag.equalsIgnoreCase("phw-img-ctr")) {
            styleDoc.append(OVERLAY_SETTINGS, overlaySettings);
        }
        styleDoc.append(THEME, theme);
        if (fromDetach) {
            styleDoc.remove("savedStyle");
        }
        mongoManager.upsert(query, new Document("$set", styleDoc), CANVAS_SITE_CUSTOM_STYLES, db);
        //TODO: handle cardhover states in font size calculation for other devices
        if (!refNum.equalsIgnoreCase(CANVUS_REFNUM) && deviceType.equalsIgnoreCase("desktop") && data.containsKey("font-size")) {
            addCalculatedValuesToDevices(refNum, theme, tag, styleId, state, MOBILE, data.get("font-size").toString(), "font-size", displayName, savedStyle, fromDetach, cardBlockStyleId, false, false);
            addCalculatedValuesToDevices(refNum, theme, tag, styleId, state, "tab", data.get("font-size").toString(), "font-size", displayName, savedStyle, fromDetach, cardBlockStyleId, false, false);
        }
        if (oldStyleId != null && oldStyleId.startsWith("phw-g-i-")) {
            addOldStyleIdInCanvasStylesQueue(refNum, oldStyleId);
        }
        //TODO: Should not send/use query into below method as we are using the same to upsert the documents
        //If any changes to the query made in future, might break styleDocs/create duplicates/corrupt the data
        if (state != null || !deviceType.equalsIgnoreCase(DESKTOP)) {
            addIfNormalStateDocIsMissing(new Document(query), displayName, savedStyle, fromDetach, false, false);
        }
        // adding system/custom type to stylescdnUrl
        Document systemQuery = new Document(REFNUM, refNum);
        systemQuery.put(THEME, theme);
        systemQuery.put(TAG, tag);
        systemQuery.put(STYLE_ID, styleId);
        //TODO: Verify this part
        type = "custom";
        if (mongoManager.checkIfDocumentExists(CANVAS_SITE_CUSTOM_STYLES, db, new Document(REFNUM, CANVUS_REFNUM).append(TAG, tag).append(STYLE_ID, styleId))) {
            type = "system";
        } else if (styleDoc.containsKey(TYPE)) {
            type = styleDoc.getString(TYPE);
        } else if (!styleDoc.containsKey(TYPE)) {
            systemQuery.put(TYPE, new Document("$exists", true));
            Document docWithType = mongoManager.findDocument(CANVAS_SITE_CUSTOM_STYLES, db, systemQuery);
            if (docWithType != null) {
                type = docWithType.getString(TYPE);
            } else {
                logger.info("docwithtype is null --> {}", systemQuery);
            }
        }
        systemQuery.remove(TYPE);
        if (fromDetach) {
            type = "custom";
            mongoManager.unsetFields(systemQuery, new Document("$unset", new Document("savedStyle", 1)), CANVAS_SITE_CUSTOM_STYLES, db);
        }
        Document updateDoc = new Document("type", type);
        if (!fromDetach && isSavedStyle(refNum, theme, styleId)) {
            updateDoc.put("savedStyle", true);
        }
        mongoManager.updateMany(systemQuery, updateDoc, CANVAS_SITE_CUSTOM_STYLES, db);
        logger.info("scheduled attribute usage data generation task for updateStyle!");
    }

    public void updateWidgetStylesv2(String refNum, String locale, String siteVariant, String pageId, String instanceId, Map<String, Object> data, boolean globalWidget, boolean isHF, String widgetType, String device) {
        Document query = getSettingsQuery(refNum, locale, siteVariant, pageId, instanceId, globalWidget, isHF);
        Document settingDoc = new Document(query);
        settingDoc.append("settings", data);
        Set<String> styleIdsSet = new HashSet<>();
        widgetUtil.extractStyleIds(data, styleIdsSet);
        settingDoc.append(STYLE_IDS, styleIdsSet);
        mongoManager.upsert(query, new Document("$set", settingDoc), CANVAS_SITE_INSTANCE_SETTINGS, db);
        if (!widgetType.equalsIgnoreCase("static")) {
            updateMetaDataInCaasDb(CANVAS_SITE_INSTANCE_SETTINGS, query, settingDoc);
        }
        if (instanceId.startsWith("hf-")) {
            setPageHFHasEditTrue(refNum, locale, instanceId);
            updateHFPublishStatus(refNum, locale, instanceId);
        }
        if (globalWidget) {
            setGlobalWidgetHasEditTrue(refNum, null, instanceId);
        }
    }

    public String getBase64Image(String base64ImageUrl) {
        try {
            String imageFile = "/tmp/_" + System.currentTimeMillis();
            if (base64ImageUrl.contains(".")) {
                imageFile = imageFile + base64ImageUrl.substring(base64ImageUrl.lastIndexOf("."));
            }
            siteUtil.downloadFile(base64ImageUrl, imageFile);
            File file = new File(imageFile);
            String base64Image = "";
            try (FileInputStream imageInFile = new FileInputStream(file)) {
                // Reading a Image file from file system
                byte[] imageData = new byte[(int) file.length()];
                imageInFile.read(imageData);
                base64Image = Base64.getEncoder().encodeToString(imageData);
                return base64Image;
            } catch (Exception e) {
                logger.error("couldn't getBase64Image -->{}", e);
            }
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
        return null;
    }

    public Map<String, Integer> getVariablesUsage(String refNum, String group, String theme) {
        try {
            Document query = new Document(REFNUM, refNum).append(GROUP, group).append(THEME, theme);
            Map<String, Integer> result = new HashMap<>();
            List<Document> docs = mongoManager.findAllDocuments(MongoConstansts.CANVAS_SITE_GLOBAL_BRANDING_VARIABLES, db, query, Arrays.asList(NAME));
            List<String> variableNames = new ArrayList<>();
            if (!docs.isEmpty() && docs != null) {
                variableNames = docs.stream().map(doc -> doc.getString("name").toLowerCase()).collect(Collectors.toList());
                variableNames.forEach(variable -> {
                    List<String> styleIds = presetService.getStyleId(refNum, variable, group);
                    result.put(variable, styleIds.size());
                });
            }
            return result;
        } catch (Exception e) {
            logger.error("Exception in getVariablesUsage refNum {} ", refNum);
            return null;
        }
    }

    public void addHiddenClassInMobilePageDuringDelete(String refNum, String locale, String pageId, String instanceId, Element sectionEle) {
        logger.debug("In addHiddenClassInMobilePageDuringDelete method for refNum : {}, locale : {}, pageId : {}, instanceId : {}", refNum, locale, pageId, instanceId);
        String desktopKey = SiteUtil.constructPhPageKey(refNum, Constants.DESKTOP, locale, pageId);
        String desktopContent = redisManager.get(desktopKey);
        Page desktopPage = Json.fromJson(Json.parse(desktopContent), Page.class);
        org.jsoup.nodes.Document desktopDoc = HtmlParser.parse(desktopPage.getPageHtml());
        Element desktopSectionEle = desktopDoc.getElementsByAttributeValue(INSTANCE_ID_FIELD, instanceId).first();
        if (desktopSectionEle != null) {
            if (desktopSectionEle.hasClass(DISPLAY_NONE_CLASS)) {
                desktopSectionEle.remove();
                sectionEle.remove();
                desktopPage.setPageHtml(desktopDoc.toString());
                redisManager.set(desktopKey, Json.toJson(desktopPage).toString());
            } else {
                logger.debug("Widget present in desktop too, adding hidden class");
                sectionEle.addClass("phw-d-none");
            }
        } else {
            sectionEle.remove();
        }
    }

    public boolean checkIfThemeExists(String refNum, String theme) {
        Document query = new Document(REFNUM, refNum).append(THEMENAME, theme);
        Document idQuery = new Document(REFNUM, refNum).append(THEME_ID, theme);
        return (mongoManager.checkIfDocumentExists(CANVAS_SITE_THEMES_METADATA, db, query) || mongoManager.checkIfDocumentExists(CANVAS_SITE_THEMES_METADATA, db, idQuery));
    }

    public Document createContentForSavedWidget(Document contentDoc) {
        String refNum = contentDoc.getString(REFNUM);
        String locale = contentDoc.getString(LOCALE);
        String variant = contentDoc.getString(PERSONA);
        String contentToBeProcessed = contentDoc.getString(TAG_CONTENT);
        String htmlPattern = "<(\"[^\"]*\"|'[^']*'|[^'\">])*>.*</(\"[^\"]*\"|'[^']*'|[^'\">])*>|<(\"[^\"]*\"|'[^']*'|[^'\">])*/?>";
        Pattern pattern = Pattern.compile(htmlPattern, Pattern.DOTALL);
        Matcher matcher = pattern.matcher(contentToBeProcessed);
        if (matcher.matches()) {
            org.jsoup.nodes.Document elementDoc = HtmlParser.parse(contentToBeProcessed);
            Elements anchorElements = elementDoc.getElementsByTag("a");
            anchorElements.forEach(anchorEle -> {
                if (anchorEle != null) {
                    anchorEle.attr(DATA_PS, contentDoc.get(DATA_PS).toString());
                    anchorEle.attr(INSTANCE_ID, contentDoc.get(INSTANCE_ID_FIELD).toString());
                    anchorEle.attr("page-id", "");
                    generateAndSetContentForCKElinks(anchorEle, refNum, Optional.of(refNum), locale, variant);
                    anchorEle.removeAttr("page-id");
                    anchorEle.removeAttr(INSTANCE_ID);
                    anchorEle.removeAttr(DATA_PS);
                }
            });
            contentDoc.put(TAG_CONTENT, elementDoc.toString());
        }
        if (contentDoc.get("contentKey").equals(HREF) || contentDoc.get("contentKey").equals(PH_HREF)) {
            String contentIdPattern = "\\b[a-zA-Z0-9]{8}-[a-zA-Z0-9]{8}\\b";
            Matcher idMatcher = Pattern.compile(contentIdPattern, Pattern.DOTALL).matcher(contentToBeProcessed);
            if (idMatcher.find()) {
                logger.info("already contentId : {} generated!", contentToBeProcessed);
                return contentDoc;
            }
            Element el = new Element(Tag.valueOf("a"), "");
            el.attr(HREF, contentToBeProcessed);
            el.attr(DATA_PS, contentDoc.get(DATA_PS).toString());
            el.attr(INSTANCE_ID, contentDoc.get(INSTANCE_ID_FIELD).toString());
            el.attr("page-id", contentDoc.containsKey(PAGE_ID) ? contentDoc.getString(PAGE_ID) : "");
            generateAndSetContentForCKElinks(el, refNum, Optional.empty(), locale, variant);
            if (el.hasAttr(CONTENT_ID)) {
                contentDoc.put(TAG_CONTENT, el.attr(CONTENT_ID));
            }
            if (contentDoc.get(CONTENT_KEY).equals(PH_HREF)) {
                contentDoc.put(CONTENT_KEY, HREF);
            }
            contentDoc.put("isCaas", true);
        }
        return contentDoc;
    }

    public String generateAndSetContentForCKElinks(Element element, String siteRefnum, Optional<String> srcRefNum, String locale, String variant) {
        try {
            logger.debug("generateAndSetContent for element {} ", element);
            Pattern MAILTO = Pattern.compile(MAILTO_REGEX);
            Pattern URL_REGEX = Pattern.compile(Constants.URL_REGEX);
            Pattern ANCHOR_REGEX = Pattern.compile(Constants.ANCHOR_REGEX);
            List<String> extensions = Arrays.asList("doc", "docx", "odt", "pdf", "rtf", "tex", "txt", "jpg", "png");
            String type;
            Document contentMapDoc = mongoManager.findDocument(Constants.CREATE_CONTENT_PAYLOAD, db, new Document("type", "link"));
            Map<String, Object> contentPayload = Json.fromJson(Json.toJson(contentMapDoc.get("payload")), Map.class);
            List<Map<String, Object>> contentValueMapList = Json.fromJson(Json.toJson(contentPayload.get(VALUE)), List.class);
            String ariaLabel = "";
            String pageId = "";
            String linkUrl = "";
            String phHrefValue = "";
            if (element.hasAttr(PH_HREF)) {
                phHrefValue = element.attr(PH_HREF);
            } else if (element.hasAttr(HREF)) {
                phHrefValue = element.attr(HREF);
            }
            if (phHrefValue.startsWith("../"))
                return null;
            if (phHrefValue.startsWith("./")) {
                phHrefValue = phHrefValue.replace("./", "");
            }
            if (element.hasAttr("aria-label")) {
                ariaLabel = element.attr("aria-label");
            }
            if (StringUtils.isNotEmpty(phHrefValue) && !element.hasAttr(CONTENT_ID)) {
                contentPayload.put(REFNUM, siteRefnum);
                contentPayload.put("locale", locale);
                contentPayload.put("siteVariant", variant);
                String fileExtension = phHrefValue.split("\\.")[phHrefValue.split("\\.").length - 1];
                Matcher mailMatcher = MAILTO.matcher(phHrefValue);
                Matcher urlMatcher = URL_REGEX.matcher(phHrefValue);
                Matcher anchorMatcher = ANCHOR_REGEX.matcher(phHrefValue);
                if (mailMatcher.matches()) {
                    type = "email";
                } else if (StringUtils.isNotEmpty(fileExtension) && urlMatcher.matches() && extensions.contains(fileExtension)) {
                    type = "files";
                } else if (urlMatcher.matches()) {
                    type = "weburl";
                } else if (anchorMatcher.matches()) {
                    type = ANCHOR;
                    pageId = siteUtil.getPageIdFromUrl(siteRefnum, srcRefNum, locale, variant, DESKTOP, phHrefValue.split("#")[0]);
                } else {
                    type = INNERPAGE;
                    pageId = siteUtil.getPageIdFromUrl(siteRefnum, srcRefNum, locale, variant, DESKTOP, phHrefValue);
                }
                if (element.hasAttr(INSTANCE_ID)) {
                    logger.debug("the content {} of pageId : {}, instance-id : {}, data-ps : {} of element has type : {} link", phHrefValue, element.attr("page-id"), element.attr(INSTANCE_ID), element.attr(DATA_PS), type);
                } else {
                    logger.debug("the content {} of element has type : {} link", phHrefValue, type);
                }
                String finalAriaLabel = ariaLabel;
                String finalPageId = pageId;
                if (type.equals(INNERPAGE) || type.equals(ANCHOR)) {
                    linkUrl = siteUtil.getPageUrl(siteRefnum, Constants.DESKTOP, locale, finalPageId, variant);
                    if (linkUrl == null || linkUrl.isEmpty()) {
                        logger.debug("innerpage link is not valid");
                        return null;
                    }
                }
                //                if(type.equals("email") && linkUrl.contains("mailto:")){
                //                    phHrefValue = phHrefValue.replaceFirst("^mailto:", "");
                //                }
                String finalLinkUrl = linkUrl;
                String finalPhHrefValue = phHrefValue;
                contentValueMapList.forEach(valueMap -> {
                    String valueName = valueMap.get("name").toString();
                    if (valueName.equals(HREF)) {
                        if (type.equals(INNERPAGE)) {
                            valueMap.put(VALUE, finalLinkUrl);
                        } else if (type.equals(ANCHOR)) {
                            valueMap.put(VALUE, finalPhHrefValue.split("#")[1]);
                        } else {
                            valueMap.put(VALUE, finalPhHrefValue);
                        }
                    } else if (valueName.equals("linkType")) {
                        valueMap.put(VALUE, type);
                    } else if (valueName.equals("ariaLabel") && StringUtils.isNotEmpty(finalAriaLabel)) {
                        valueMap.put(VALUE, finalAriaLabel);
                    } else if (valueName.equals("innerPageLocale") && (type.equals(INNERPAGE) || type.equals(ANCHOR))) {
                        valueMap.put(VALUE, locale);
                    } else if (valueName.equals("innerPagePersona") && (type.equals(INNERPAGE) || type.equals(ANCHOR))) {
                        valueMap.put(VALUE, variant);
                    } else if (valueName.equals(PAGE_ID) && (type.equals(INNERPAGE) || type.equals(ANCHOR))) {
                        valueMap.put(VALUE, finalPageId);
                    }
                });
                contentPayload.put(HAS_EDIT, true);
                contentPayload.put(VALUE, contentValueMapList);
                if ((type.equals(INNERPAGE) || type.equals(ANCHOR)) && (!siteUtil.isValidString(locale) || !siteUtil.isValidString(variant) || !siteUtil.isValidString(pageId))) {
                    logger.debug("innerpage link is not valid");
                    return null;
                }
                String url = conf.getString(CREATE_CONTENT_URL);
                JsonNode resp = siteUtil.sendPostSync(url, contentPayload);
                if (resp != null && resp.get("status").textValue().equals(SUCCESS)) {
                    String contentId = resp.get("data").asText();
                    element.attr(CONTENT_ID, contentId);
                    logger.debug("content created with id : {} ", contentId);
                    return contentId;
                }
            }
        } catch (Exception e) {
            logger.debug("Error in generateAndSetContentForCKEedits: ", e);
        }
        return null;
    }

    public boolean isPCMEnabledTenant(String refNum, String tag) {
        Document queryDoc = new Document(REFNUM, refNum);
        queryDoc.put(TAG, tag);
        queryDoc.put(ENABLED, true);
        return mongoManager.checkIfDocumentExists(Constants.CANVAS_SITE_PCM_ENABLED_TENANTS, db, queryDoc);
    }

    public Map<String, Object> getChangedCssProperties(String refNum, String tag, String deviceType, String styleId, String theme, String state, String cardBlockStyleId, Map<String, Object> newProperties) {
        try {
            Document query = new Document(REFNUM, refNum);
            query.append(TAG, tag);
            query.append(DEVICE_TYPE, deviceType);
            query.append(STYLE_ID, styleId);
            query.append(THEME, theme);
            if (state != null) {
                query.append("state", state);
                if (state.equalsIgnoreCase("cardHover")) {
                    query.append("cardBlockStyleId", cardBlockStyleId);
                }
            } else {
                query.append("state", new Document("$exists", false));
            }
            Document styleDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
            Map<String, Object> editedStyles = (styleDoc == null || !styleDoc.containsKey(EDITED_STYLES)) ? new HashMap<>() : (Map<String, Object>) styleDoc.get(EDITED_STYLES);
            Map<String, Object> oldProperties = (styleDoc == null || !styleDoc.containsKey("styles")) ? new HashMap<>() : (Map<String, Object>) styleDoc.get("styles");
            if (editedStyles != null) {
                Set<String> editedCss = new HashSet<>();
                editedCss.addAll(editedStyles.keySet());
                for (String key : editedCss) {
                    if (!newProperties.containsKey(key)) {
                        editedStyles.remove(key);
                    }
                }
            }
            Map<String, Object> changedProperties = new HashMap<>();
            for (String key : newProperties.keySet()) {
                if ((editedStyles != null && editedStyles.containsKey(key)) || (!oldProperties.containsKey(key) || !oldProperties.get(key).equals(newProperties.get(key)))) {
                    changedProperties.put(key, newProperties.get(key));
                }
            }
            return changedProperties;
        } catch (Exception e) {
            logger.error("couldn't getChangedCssProperties -->{}", e);
        }
        return new HashMap<>();
    }

    public StringBuffer getSuffix(String styleId, String tag, String suffix, Map<String, Object> data, Map<String, Object> extraClasses, Map<String, Object> extraClassesToAdd, Map<String, String> identifierMappings, Map<String, String> sliderClassExtension, List<String> inputTypes, StringBuffer br, boolean isMigratedSite, String cardBlockStyleId, String state, List<String> tagDefaultClasses, List<String> pcmEnabledTags, boolean generateCssWithId, boolean fromBuildStyles) {
        try {
            if (!styleId.equalsIgnoreCase(DEFAULT)) {
                String typeStr = "";
                if (inputTypes.contains(tag)) {
                    typeStr = "[type='" + tag + "']";
                }
                String sliderExtensionCls = "";
                for (String startsWithclass : sliderClassExtension.keySet()) {
                    if (styleId.startsWith(startsWithclass)) {
                        sliderExtensionCls = sliderClassExtension.get(startsWithclass);
                    }
                }
                String stylePrefix = (generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag) && !((cardBlockStyleId != null && state != null && state.equalsIgnoreCase("cardHover"))) ? (PCM_SELECTOR + " .") : ".") + styleId + typeStr + sliderExtensionCls + suffix + "{" + "\n";
                if (cardBlockStyleId != null && state != null && state.equalsIgnoreCase("cardHover")) {
                    stylePrefix = ((generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " .") : ".") + cardBlockStyleId + ":hover " + stylePrefix;
                }
                if (extraClasses.containsKey(styleId) && (!isMigratedSite || (isMigratedSite && generateCssWithId && !fromBuildStyles))) {
                    //                stylePrefix = "." + styleId + suffix + "," + "." + extraClasses.get(styleId) + suffix + "{" + "\n";
                    stylePrefix = ((generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " .") : ".") + styleId + suffix;
                    List<String> extraClassValues = (List<String>) extraClasses.get(styleId);
                    for (String eachExtraClass : extraClassValues) {
                        stylePrefix = stylePrefix + "," + ((generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " .") : ".") + eachExtraClass + typeStr + suffix;
                    }
                    //                    stylePrefix = stylePrefix + suffix;
                    if (!extraClassesToAdd.containsKey(styleId)) {
                        stylePrefix = stylePrefix + "{" + "\n";
                    }
                }
                if (extraClassesToAdd.containsKey(styleId) && generateCssWithId && !fromBuildStyles) {
                    if (!extraClasses.containsKey(styleId)) {
                        stylePrefix = ((isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " .") : ".") + styleId + typeStr + suffix;
                    }
                    List<String> classes = (List<String>) ((Map<String, Object>) extraClassesToAdd.get(styleId)).get("classes");
                    for (String eachExtraClass : classes) {
                        stylePrefix = stylePrefix + "," + ((isMigratedSite && pcmEnabledTags.contains(tag)) ? PCM_SELECTOR : "" + " ") + eachExtraClass + typeStr + suffix;
                    }
                    stylePrefix = stylePrefix + "{" + "\n";
                }
                br.append(stylePrefix);
            } else {
                if (inputTypes.contains(tag)) {
                    br.append(("") + "input[type='" + tag + "']" + suffix + "{" + "\n");
                } else if (tag.equalsIgnoreCase("p")) {
                    br.append(("") + "[data-tag-type='p']," + tag + "{" + "\n");
                } else {
                    if (tagDefaultClasses.contains(tag)) {
                        br.append(((generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " .") : "."));
                    }
                    if (identifierMappings.containsKey(tag)) {
                        br.append(tag + suffix + ", " + ((generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " .") : ".") + identifierMappings.get(tag) + suffix + "{" + "\n");
                    } else {
                        br.append(((generateCssWithId && isMigratedSite && pcmEnabledTags.contains(tag)) ? (PCM_SELECTOR + " ") : "") + tag + suffix + "{" + "\n");
                    }
                }
            }
            return br;
        } catch (Exception e) {
            logger.error("couldn't get suffix -->{}", e);
        }
        return null;
    }

    public StringBuffer constructInnerCss(List<String> stylesAscendingOrder, List<String> imgStyles, List<String> commonImgStyles, String styleId, String tag, Map<String, Object> data, Map<String, Object> overlaySettings, StringBuffer br, boolean isMigratedSite, boolean[] addImportantToSpecificStyle, List<String> stylesToRestrict, Map<String, Object> changedCssProperties, boolean generateCssWithId, boolean fromBuildStyles) {
        try {
            if (stylesAscendingOrder != null) {
                for (String key : stylesAscendingOrder) {
                    if (tag.equalsIgnoreCase(PHW_IMG_CTR) && imgStyles.contains(key)) {
                        continue;
                    }
                    if (data.containsKey(key)) {
                        Object value = data.get(key);
                        if (tag.equalsIgnoreCase("phw-btn")) {
                            if (key.equalsIgnoreCase("height") && !data.containsKey("min-height")) {
                                key = "min-height";
                            }
                        }
                        if (!key.startsWith("act-p-") && !stylesToRestrict.contains(key)) {
                            br.append(key + ":" + value + (((!fromBuildStyles && isMigratedSite && changedCssProperties.containsKey(key)) || addImportantToSpecificStyle[0]) ? " !important" : "") + ";" + "\n");
                        }
                    }
                }
            }
            List<String> finalImgStyles = imgStyles;
            finalImgStyles.addAll(commonImgStyles);
            data.forEach((key, value) -> {
                if (stylesAscendingOrder.contains(key)) {
                    return;
                }
                if (tag.equalsIgnoreCase(PHW_IMG_CTR) && finalImgStyles.contains(key)) {
                    return;
                }
                if (tag.equalsIgnoreCase("phw-btn")) {
                    if (key.equalsIgnoreCase("height") && !data.containsKey("min-height")) {
                        key = "min-height";
                    }
                }
                if (!key.startsWith("act-p-") && !stylesToRestrict.contains(key)) {
                    br.append(key + ":" + value + (((!fromBuildStyles && isMigratedSite && changedCssProperties.containsKey(key)) || addImportantToSpecificStyle[0]) ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                }
            });
            br.append("}" + "\n");
            if (overlaySettings != null && overlaySettings.size() > 0) {
                String overlayStylePrefix = "." + styleId + "::before" + "{" + "\n";
                br.append(overlayStylePrefix);
                overlaySettings.forEach((eachKey, eachValue) -> {
                    br.append(eachKey + ":" + eachValue + (((!fromBuildStyles && isMigratedSite && changedCssProperties.containsKey(eachKey)) || addImportantToSpecificStyle[0]) ? " !important" : "") + ";" + "\n");
                });
                br.append("}" + "\n");
            }
            if (tag.equalsIgnoreCase(PHW_IMG_CTR)) {
                String imgStylePrefix = (".") + styleId + " img" + "{" + "\n";
                br.append(imgStylePrefix);
                finalImgStyles.forEach(eachKey -> {
                    if (data.containsKey(eachKey)) {
                        br.append(eachKey + ":" + data.get(eachKey).toString() + (((!fromBuildStyles && isMigratedSite && changedCssProperties.containsKey(eachKey)) || addImportantToSpecificStyle[0]) ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                    }
                });
                // objectfit empty  -> add objectfit:cover
                if (!data.containsKey(OBJECT_FIT)) {
                    br.append("object-fit:cover " + (((!fromBuildStyles && isMigratedSite && changedCssProperties.containsKey(OBJECT_FIT)) || addImportantToSpecificStyle[0]) ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                }
                // objectfit none -> remove height and width
                if (data.containsKey(HEIGHT) && !(data.containsKey(OBJECT_FIT) && "unset".equalsIgnoreCase(data.get(OBJECT_FIT).toString()))) {
                    br.append("height:100% " + (((!fromBuildStyles && isMigratedSite) || addImportantToSpecificStyle[0]) ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                    br.append("width:100% " + (((!fromBuildStyles && isMigratedSite) || addImportantToSpecificStyle[0] && !fromBuildStyles) ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                }
                br.append("}" + "\n");
                if (isMigratedSite && data.containsKey(HEIGHT)) {
                    imgStylePrefix = (" .") + styleId + " ppc-container" + "{" + "\n";
                    br.append(imgStylePrefix);
                    br.append("height:inherit " + (!fromBuildStyles ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                    br.append("display:inline-block " + (!fromBuildStyles ? (" " + NOT_IMPORTANT) : "") + ";" + "\n");
                    br.append("}" + "\n");
                }
            }
            return br;
        } catch (Exception e) {
            logger.error("couldn't constructInnerCss -->{}", e);
        }
        return null;
    }

    public boolean bulkUpdateCustomStylesCss(String refNum, List<String> themes) {
        try {
            themes.forEach(theme -> {
                addCustomStylesCssToPage(refNum, theme);
            });
            return true;
        } catch (Exception e) {
            logger.error("couldn't bulkUpdateCustomStylesCss -->{}", e);
        }
        return false;
    }

    public boolean generateCss(String refNum, List<String> themes, List<String> tags, boolean generateCssWithId, boolean fromBuildStyles) {
        try {
            themes.forEach(theme -> {
                Document queryDoc = new Document(REFNUM, refNum).append(THEME, theme);
                if (tags != null) {
                    queryDoc.put(TAG, new Document("$in", tags));
                }
                List<String> projections = Arrays.asList("styles", STYLE_ID, TAG, STATE, DEVICE_TYPE, CARD_BLOCK_STYLE_ID, ID);
                List<Document> docs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, queryDoc, projections);
                docs.forEach(doc -> {
                    String css = constructCss((Map<String, Object>) doc.get("styles", Map.class), doc.getString(STYLE_ID), doc.getString(TAG), doc.containsKey(STATE) ? doc.getString("state") : null, doc.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), doc.containsKey(CARD_BLOCK_STYLE_ID) ? doc.getString("cardBlockStyleId") : null, refNum, generateCssWithId, theme, null, fromBuildStyles);
                    mongoManager.findOneAndUpdate(new Document(ID, doc.getObjectId(ID)), new Document("css", css), Constants.CANVAS_SITE_CUSTOM_STYLES, db);
                });
                addCustomStylesCssToPage(refNum, theme);
            });
            return true;
        } catch (Exception e) {
            logger.error("couldn't bulkUpdateCustomStylesCss -->{}", e);
        }
        return false;
    }

    public boolean updateCssForMigratedSites(String targetRefNum, String srcRefNum, List<String> themes) {
        try {
            themes.forEach(theme -> {
                Document queryDoc = new Document(REFNUM, srcRefNum).append(THEME, theme);
                queryDoc.put("tag", "phw-btn");
                queryDoc.put("savedStyle", true);
                List<Document> docs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, queryDoc);
                docs.forEach(doc -> {
                    Document targetQueryDoc = new Document(REFNUM, targetRefNum).append(THEME, theme);
                    targetQueryDoc.put("tag", "phw-btn");
                    targetQueryDoc.put("savedStyle", true);
                    targetQueryDoc.put(STYLE_ID, doc.getString(STYLE_ID));
                    targetQueryDoc.put(DEVICE_TYPE, doc.getString(DEVICE_TYPE));
                    targetQueryDoc.put(STATE, doc.containsKey(STATE) ? doc.getString(STATE) : new Document("$exists", false));
                    targetQueryDoc.put("cardBlockStyleId", doc.containsKey("cardBlockStyleId") ? doc.getString("cardBlockStyleId") : new Document("$exists", false));
                    List<Document> targetDocs = mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, targetQueryDoc);
                    if (targetDocs != null && !targetDocs.isEmpty()) {
                        if (targetDocs.size() > 1) {
                            logger.error("More than one document found for targetQueryDoc --> {}", targetQueryDoc);
                            return;
                        } else {
                            Document targetDoc = targetDocs.get(0);
                            targetDoc.remove(ID);
                            if (targetDoc != null) {
                                Map<String, Object> currentStyles = (Map<String, Object>) targetDoc.get("styles", Map.class);
                                Map<String, Object> oldStyles = (Map<String, Object>) doc.get("styles", Map.class);
                                Map<String, Object> editedStyles = new HashMap<>();
                                if (doc.containsKey(EDITED_STYLES)) {
                                    editedStyles = (Map<String, Object>) doc.get(EDITED_STYLES);
                                }
                                for (String cssProp : currentStyles.keySet()) {
                                    if (editedStyles.containsKey(cssProp)) {
                                        return;
                                    } else if (oldStyles.containsKey(cssProp) && !oldStyles.get(cssProp).equals(currentStyles.get(cssProp))) {
                                        editedStyles.put(cssProp, currentStyles.get(cssProp));
                                    } else if (!oldStyles.containsKey(cssProp)) {
                                        editedStyles.put(cssProp, currentStyles.get(cssProp));
                                    }
                                }
                                targetDoc.put(EDITED_STYLES, editedStyles);
                                mongoManager.findOneAndUpdate(targetQueryDoc, targetDoc, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
                                String css = constructCss((Map<String, Object>) targetDoc.get("styles", Map.class), doc.getString(STYLE_ID), doc.getString(TAG), doc.getString("state"), doc.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), doc.getString("cardBlockStyleId"), targetRefNum, true, theme, null, false);
                                targetDoc.put("css", css);
                                mongoManager.findOneAndUpdate(targetQueryDoc, targetDoc, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
                            }
                        }
                    }
                });
                addCustomStylesCssToPage(targetRefNum, theme);
            });
            return true;
        } catch (Exception e) {
            logger.error("couldn't bulkUpdateCustomStylesCss -->{}", e);
        }
        return false;
    }

    public void updatePCMChangesForOtherDevicesIfEditedInDesktop(String refNum, String theme, String tag, String styleId, String state, String deviceType, boolean generateCssWithId, boolean fromBuildStyles) {
        if (isCanvasMigratedSite(refNum, Optional.empty())) {
            Document query = new Document(REFNUM, refNum);
            query.put(THEME, theme);
            query.put(TAG, tag);
            query.put(DEVICE_TYPE, deviceType);
            query.put(STYLE_ID, styleId);
            if (state != null) {
                query.append("state", state);
            } else {
                query.append("state", new Document("$exists", false));
            }
            Document styleDoc = mongoManager.findDocument(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query);
            List<Document> remainingDocs = new ArrayList<>();
            if (styleDoc != null) {
                if (state == null) {
                    query.put(STATE, new Document("$exists", true));
                    remainingDocs.addAll(mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query));
                }
                if (deviceType != null && deviceType.equalsIgnoreCase(DESKTOP)) {
                    query.put(DEVICE_TYPE, new Document("$ne", DESKTOP));
                    if (state == null) {
                        query.remove(STATE);
                    }
                    remainingDocs.addAll(mongoManager.findAllDocuments(Constants.CANVAS_SITE_CUSTOM_STYLES, db, query));
                }
            }
            if (styleDoc != null) {
                Map<String, Object> editedDesktopStyles = styleDoc.containsKey(EDITED_STYLES) ? (Map<String, Object>) styleDoc.get(EDITED_STYLES) : new HashMap<>();
                if (!remainingDocs.isEmpty()) {
                    remainingDocs.forEach(doc -> {
                        Map<String, Object> otherDeviceStyles = (Map<String, Object>) doc.get("styles");
                        Map<String, Object> otherDeviceEditedStyles = doc.containsKey(EDITED_STYLES) ? (Map<String, Object>) doc.get(EDITED_STYLES) : new HashMap<>();
                        if (otherDeviceStyles != null) {
                            for (String key : editedDesktopStyles.keySet()) {
                                if (otherDeviceStyles.containsKey(key) && !otherDeviceEditedStyles.containsKey(key)) {
                                    otherDeviceEditedStyles.put(key, otherDeviceStyles.get(key));
                                }
                            }
                        }
                        doc.put(EDITED_STYLES, otherDeviceEditedStyles);
                        mongoManager.findOneAndUpdate(new Document(ID, doc.getObjectId(ID)), doc, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
                        String css = constructCss((Map<String, Object>) doc.get(STYLES), styleId, tag, doc.containsKey(STATE) ? doc.getString(STATE) : null, doc.getString(DEVICE_TYPE), new HashMap<>(), new HashMap<>(), doc.containsKey(CARD_BLOCK_STYLE_ID) ? doc.getString(CARD_BLOCK_STYLE_ID) : null, refNum, generateCssWithId, theme, null, fromBuildStyles);
                        doc.put(CSS, css);
                        mongoManager.findOneAndUpdate(new Document(ID, doc.getObjectId(ID)), doc, Constants.CANVAS_SITE_CUSTOM_STYLES, db);
                    });
                }
            }
        }
    }

    public boolean isFeatureEnabled(String refNum, String collName, boolean enabled) {
        Document query = new Document(REFNUM, refNum);
        query.put(ENABLED, enabled);
        return mongoManager.checkIfDocumentExists(collName, db, query);
    }

    public void updateThemePublishStatus(String refNum, String theme) {
        Document themeQuery = new Document(REFNUM, refNum).append(THEME_ID, theme);
        Document updateDoc = new Document(UPDATED_DATE, new Date());
        Document themeDoc = mongoManager.findDocument(CANVAS_SITE_THEMES_METADATA, db, themeQuery);
        if (themeDoc != null) {
            String status = themeDoc.getString(STATUS_KEY);
            if ("published".equalsIgnoreCase(status)) {
                updateDoc.put(STATUS_KEY, "unpublished");
            }
        }
        mongoManager.upsert(themeQuery, new Document("$set", updateDoc), CANVAS_SITE_THEMES_METADATA, db);
    }

    public void updateHFPublishStatus(String refNum, String locale, String hfInstanceId) {
        Document hfQuery = new Document(REFNUM, refNum).append(LOCALE, locale).append("hfInstanceId", hfInstanceId);
        Document updateDoc = new Document(UPDATED_DATE, new Date());
        Document hfDoc = mongoManager.findDocument(CANVAS_SITE_HF_INSTANCES, db, hfQuery);
        if (hfDoc != null) {
            if (hfDoc.containsKey("publishStatus") && "published".equalsIgnoreCase(hfDoc.getString("publishStatus"))) {
                updateDoc.put("publishStatus", "unpublished");
            }
        }
        mongoManager.upsert(hfQuery, new Document("$set", updateDoc), CANVAS_SITE_HF_INSTANCES, db);
    }

    public void updateChildInstanceSettings(Map<String, Object> childInstanceVsDataPs, Map<String, Object> settings, Document queryDoc) {
        try {
            for (String childInstanceId : childInstanceVsDataPs.keySet()) {
                Map<String, Object> instanceVsDataPs = (Map<String, Object>) childInstanceVsDataPs.get(childInstanceId);
                String widgetType = (String) instanceVsDataPs.get("widgetType");
                List<String> dataPsLst = (List<String>) instanceVsDataPs.get("dataPs");
                queryDoc.put(INSTANCE_HYPHEN_ID, childInstanceId);
                Document childDoc = mongoManager.findDocument(CANVAS_SITE_INSTANCE_SETTINGS, db, queryDoc);
                Map<String, Object> childSettings = (childDoc != null) ? (Map<String, Object>) childDoc.get("settings") : new HashMap<>();
                dataPsLst.forEach(dataPs -> {
                    if (!settings.containsKey(dataPs)) {
                        return;
                    }
                    if (childSettings.containsKey(dataPs)) {
                        Map<String, Object> dataPsSetting = new HashMap<>((Map<String, Object>) settings.get(dataPs));
                        if (childSettings.get(dataPs) instanceof String) {
                            dataPsSetting.put("styleId", childSettings.get(dataPs));
                        } else {
                            dataPsSetting = (Map<String, Object>) childSettings.get(dataPs);
                            dataPsSetting.put("elementVisibility", ((Map<String, Object>) settings.get(dataPs)).get("elementVisibility"));
                        }
                        childSettings.put(dataPs, dataPsSetting);
                    } else {
                        childSettings.put(dataPs, settings.get(dataPs));
                    }
                });
                mongoManager.upsert(queryDoc, new Document("$set", new Document("settings", childSettings)), CANVAS_SITE_INSTANCE_SETTINGS, db);
                if ("functional".equalsIgnoreCase(widgetType)) {
                    updateMetaDataInCaasDb(CANVAS_SITE_INSTANCE_SETTINGS, queryDoc, new Document("settings", childSettings));
                }
            }
        } catch (Exception e) {
            logger.error("couldn't updateChildInstanceSettings -->{}", e);
        }
    }
}
